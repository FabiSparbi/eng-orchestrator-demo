"""The model path: same contract as the rule extractor, real understanding.

The rule extractor in `extraction.py` is what makes this demo runnable with no
credentials, but keyword lists do not survive real site notes ("die Fuge unten
rechts ist hinueber" is a defect and matches nothing). This module sends the
customer's schema and the captured notes to the shared Foundry deployment and
asks for the same cell-shaped answer.

TWO RULES THE PROMPT ENFORCES AND THE PARSER RE-CHECKS
------------------------------------------------------
1. NO INVENTED VALUES. Every filled cell must name the note ids it came from;
   a cell without evidence is dropped back to empty here, whatever the model
   said. That check is in code because the model cannot be trusted with it.
2. ALLOWED VALUES ONLY. A choice column takes one of the schema's values or
   nothing.

STATUS: written against the same client the four agents use
(`common.llm_client.get_shared_chat_client`), but NOT yet exercised against a
live deployment -- there is no Foundry endpoint in this environment. Treat it
as the integration point, not as verified behaviour. The rule extractor stays
the default until this has been run against a real deployment.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from ki_protokoll.extraction import extract_table
from ki_protokoll.models import Cell, Inspection, ProtocolTable
from ki_protokoll.schema import Field, TableSchema

INSTRUCTIONS = (
    "Du erstellst Besichtigungsprotokolle im Tabellenformat eines Kunden. "
    "Du bekommst das Tabellenschema und alle Aufnahmen einer Besichtigung "
    "(Textnotizen, Transkripte von Sprachnotizen, Bildbeschreibungen). "
    "Fuelle die Tabelle ausschliesslich aus diesem Material.\n\n"
    "Harte Regeln:\n"
    "1. Erfinde nichts. Was nicht im Material steht, bleibt leer (wert=null).\n"
    "2. Jede gefuellte Zelle nennt die Notiz-IDs, aus denen sie stammt.\n"
    "3. Bei Auswahlspalten ist nur einer der erlaubten Werte zulaessig.\n"
    "4. konfidenz ist 0.0-1.0 und ehrlich: aus einem Bild abgeleitete Angaben "
    "liegen unter 0.7, klare O-Toene darueber. Widerspruechliches Material "
    "bekommt eine niedrige Konfidenz und eine Begruendung.\n"
    "5. Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema."
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _field_spec(f: Field) -> dict[str, Any]:
    spec: dict[str, Any] = {"id": f.id, "spalte": f.label, "typ": f.type, "pflicht": f.mandatory}
    if f.allowed:
        spec["erlaubteWerte"] = f.allowed
    return spec


def build_prompt(inspection: Inspection, schema: TableSchema) -> str:
    """The user message: schema, positions and the raw captured material."""
    payload = {
        "tabellenschema": {
            "titel": schema.title,
            "kopf": [_field_spec(f) for f in schema.header_fields],
            "positionsspalten": [_field_spec(f) for f in schema.position_fields],
        },
        "stammdaten": inspection.metadata,
        "positionen": inspection.positions(),
        "aufnahmen": [
            {"id": n.id, "art": n.kind_label, "position": n.position, "inhalt": n.content}
            for n in inspection.notes
        ],
        "antwortformat": {
            "kopf": {"<feld-id>": {"wert": "...", "konfidenz": 0.9, "quellen": ["N-001"], "begruendung": "..."}},
            "positionen": [
                {
                    "position": "<eine der Positionen>",
                    "felder": {
                        "<feld-id>": {
                            "wert": "...",
                            "konfidenz": 0.9,
                            "quellen": ["N-002"],
                            "begruendung": "...",
                        }
                    },
                }
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _cell_from_model(field: Field, raw: dict[str, Any] | None) -> Cell | None:
    """Accept a model-produced cell only if it obeys the two hard rules."""
    if not isinstance(raw, dict):
        return None
    value = raw.get("wert")
    if value in (None, "", "null"):
        return None

    sources = [str(s) for s in raw.get("quellen", []) if str(s).strip()]
    if not sources:
        # Rule 1, enforced in code: no evidence, no cell.
        return Cell(field_id=field.id, value=None, confidence=0.0, reason="Modell nannte keine Quelle")

    if field.allowed and str(value) not in field.allowed:
        return Cell(
            field_id=field.id,
            value=None,
            confidence=0.0,
            reason=f"Modellwert '{value}' ist kein erlaubter Wert dieser Spalte",
        )

    try:
        confidence = float(raw.get("konfidenz", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return Cell(
        field_id=field.id,
        value=str(value),
        confidence=max(0.0, min(1.0, confidence)),
        sources=sources,
        reason=str(raw.get("begruendung", "")),
    )


def parse_response(text: str, inspection: Inspection, schema: TableSchema) -> ProtocolTable:
    """Turn the model's JSON into a table, falling back per cell, not per run.

    Anything the model left out or got wrong is filled by the rule extractor,
    so a partial model answer degrades gracefully instead of losing the run.
    """
    match = _JSON_BLOCK.search(text)
    if not match:
        raise ValueError("Antwort des Modells enthielt kein JSON-Objekt.")
    data = json.loads(match.group(0))

    table = extract_table(inspection, schema)  # baseline, then overwrite what the model got right

    header = data.get("kopf", {})
    for f in schema.header_fields:
        cell = _cell_from_model(f, header.get(f.id))
        if cell is not None:
            table.header[f.id] = cell

    by_position = {str(p.get("position")): p.get("felder", {}) for p in data.get("positionen", [])}
    for row in table.rows:
        fields = by_position.get(row.position, {})
        for f in schema.position_fields:
            cell = _cell_from_model(f, fields.get(f.id))
            if cell is not None:
                row.cells[f.id] = cell

    return table


async def extract_table_with_llm_async(inspection: Inspection, schema: TableSchema) -> ProtocolTable:
    from agent_framework import Agent  # noqa: PLC0415 -- keep the SDK off the offline path

    from common.llm_client import get_shared_chat_client  # noqa: PLC0415

    agent = Agent(
        name="ProtokollExtraktion",
        instructions=INSTRUCTIONS,
        client=get_shared_chat_client(),
    )
    response = await agent.run(build_prompt(inspection, schema))
    return parse_response(response.text, inspection, schema)


def extract_table_with_llm(inspection: Inspection, schema: TableSchema) -> ProtocolTable:
    """Synchronous drop-in for `extract_table` -- pass as `extractor=`."""
    return asyncio.run(extract_table_with_llm_async(inspection, schema))


def _prose_instructions() -> str:
    return (
        "Du formulierst aus einer fertigen Protokolltabelle einen sachlichen "
        "Fliesstext auf Deutsch. Nutze ausschliesslich die Angaben der Tabelle, "
        "ergaenze nichts, und benenne offene Punkte als offen."
    )


async def generate_prose_async(table: ProtocolTable, schema: TableSchema) -> str:
    """Optional nicer prose than `render.render_prose`, from the same table."""
    from agent_framework import Agent  # noqa: PLC0415

    from common.llm_client import get_shared_chat_client  # noqa: PLC0415

    agent = Agent(name="ProtokollFliesstext", instructions=_prose_instructions(), client=get_shared_chat_client())
    payload = json.dumps(table.to_dict(), ensure_ascii=False, indent=2)
    response = await agent.run(payload)
    return response.text
