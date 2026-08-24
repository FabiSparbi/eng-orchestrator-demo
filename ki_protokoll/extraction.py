"""Deriving the customer table from notes, photos and voice notes.

WHY RULES AND NOT A MODEL (for now)
-----------------------------------
This is the offline extractor. It is deterministic, needs no credentials, and
runs in milliseconds, which is what makes the whole flow -- table, gaps,
notification, second pass -- testable and demonstrable today. It is NOT the
production answer: real notes are messier than any keyword list.

The production answer is `ki_protokoll/llm.py`, which sends the same schema and
the same notes to the shared Foundry deployment and asks for the same
`Cell`-shaped result, confidence and evidence included. Both extractors satisfy
the `Extractor` protocol below, so `service.py` does not care which one ran --
and that is deliberate: the gap detection, the to-do list and the second pass
are built on the cell confidences, not on how they were produced.

Everything downstream depends on one rule kept here: NEVER invent a value. A
field that cannot be derived stays empty with confidence 0, because an
invented cell in a customer's protocol is worse than a visible gap.
"""

from __future__ import annotations

import re
from typing import Protocol

from ki_protokoll.models import SOURCE_CONFIDENCE, Cell, Inspection, Note, ProtocolTable, Row
from ki_protokoll.schema import Field, TableSchema

# Hedged evidence ("vermutlich", "schlecht erkennbar") is evidence, but weaker.
# The penalty is what pushes a hedged voice note below the threshold so it shows
# up as "unsicher" instead of landing unremarked in the customer's table.
HEDGE_WORDS = (
    "vermutlich", "vielleicht", "evtl", "eventuell", "unklar", "unsicher",
    "schlecht erkennbar", "nicht sicher", "koennte", "moeglicherweise", "schaetze",
)
HEDGE_PENALTY = 0.25

# Confidence a cell keeps when the notes disagree with each other. Deliberately
# below any sane threshold: contradictions are for the user to resolve.
CONFLICT_CONFIDENCE = 0.4

# Corroboration bonus when several notes independently support the same value.
CORROBORATION_BONUS = 0.05
MAX_CONFIDENCE = 0.98

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")


class Extractor(Protocol):
    """What service.py needs: notes plus a schema in, a filled table out."""

    def __call__(self, inspection: Inspection, schema: TableSchema) -> ProtocolTable: ...


def fold(text: str) -> str:
    """Lowercase and fold umlauts, so 'Beschädigt' matches 'beschaedigt'."""
    lowered = text.lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        lowered = lowered.replace(src, dst)
    return lowered


def note_confidence(note: Note) -> float:
    """Base confidence for anything read out of this note."""
    base = SOURCE_CONFIDENCE.get(note.kind, 0.5)
    if any(word in fold(note.content) for word in HEDGE_WORDS):
        base -= HEDGE_PENALTY
    return round(max(0.0, base), 2)


def _evidence(note: Note) -> str:
    return f"aus {note.kind_label} {note.id}"


def _empty(field: Field, reason: str) -> Cell:
    return Cell(field_id=field.id, value=None, confidence=0.0, reason=reason)


# --------------------------------------------------------------------------
# Strategies. Each one returns a Cell for exactly one field of one row.
# --------------------------------------------------------------------------

def _running_number(field: Field, index: int) -> Cell:
    return Cell(field_id=field.id, value=str(index), confidence=1.0, sources=["system"], reason="vom System vergeben")


def _from_metadata(field: Field, inspection: Inspection) -> Cell:
    key = field.extraction.get("feld", field.id)
    value = inspection.metadata.get(key)
    if value in (None, ""):
        return _empty(field, f"Stammdatenfeld '{key}' ist im Besichtigungs-Export leer")
    return Cell(
        field_id=field.id,
        value=str(value),
        confidence=SOURCE_CONFIDENCE["system"],
        sources=["stammdaten"],
        reason="aus den Stammdaten der Besichtigung",
    )


def _from_position(field: Field, position: str) -> Cell:
    parts = [p.strip() for p in position.split("/")]
    index = int(field.extraction.get("teil", 0))
    if index >= len(parts) or not parts[index]:
        return _empty(field, f"Position '{position}' enthaelt keinen Anteil {index + 1}")
    return Cell(
        field_id=field.id,
        value=parts[index],
        confidence=SOURCE_CONFIDENCE["system"],
        sources=["position"],
        reason="bei der Aufnahme in der App gesetzt",
    )


def _from_choice_synonyms(field: Field, notes: list[Note]) -> Cell:
    """Map free speech onto the customer's allowed values."""
    synonyms: dict[str, list[str]] = field.extraction.get("synonyme", {})
    hits: dict[str, dict[str, object]] = {}

    for note in notes:
        text = fold(note.content)
        confidence = note_confidence(note)
        for value, words in synonyms.items():
            matched = next((w for w in words if fold(w) in text), None)
            if matched is None:
                continue
            entry = hits.setdefault(value, {"confidence": 0.0, "sources": [], "words": set()})
            entry["confidence"] = max(float(entry["confidence"]), confidence)  # type: ignore[arg-type]
            entry["sources"].append(note.id)  # type: ignore[union-attr]
            entry["words"].add(matched)  # type: ignore[union-attr]

    if not hits:
        return _empty(field, "keine Zustands-/Einstufungsaussage in den Notizen gefunden")

    best_value = max(hits, key=lambda v: (float(hits[v]["confidence"]), len(hits[v]["sources"])))  # type: ignore[arg-type,index]
    best = hits[best_value]
    confidence = float(best["confidence"])
    sources = list(best["sources"])  # type: ignore[arg-type]
    if len(sources) > 1:
        confidence = min(MAX_CONFIDENCE, confidence + CORROBORATION_BONUS)

    competing = sorted(v for v in hits if v != best_value)
    if competing:
        return Cell(
            field_id=field.id,
            value=best_value,
            confidence=min(confidence, CONFLICT_CONFIDENCE),
            sources=sources + [s for v in competing for s in hits[v]["sources"]],  # type: ignore[union-attr]
            reason="widerspruechliche Angaben: " + " / ".join([best_value, *competing]),
            conflict=[best_value, *competing],
        )

    words = ", ".join(sorted(str(w) for w in best["words"]))  # type: ignore[union-attr]
    return Cell(
        field_id=field.id,
        value=best_value,
        confidence=round(confidence, 2),
        sources=sources,
        reason=f"Stichwort '{words}' in {len(sources)} Notiz(en)",
    )


def _from_pattern(field: Field, notes: list[Note]) -> Cell:
    """Pull a number or a unit out of free text with the schema's regex."""
    pattern = re.compile(field.extraction["muster"], re.IGNORECASE)
    group = int(field.extraction.get("gruppe", 1))
    normalisation: dict[str, str] = field.extraction.get("normalisierung", {})

    best: tuple[float, str, Note] | None = None
    for note in notes:
        match = pattern.search(note.content)
        if not match:
            continue
        raw = (match.group(group) or "").strip()
        if not raw:
            continue
        value = normalisation.get(fold(raw), normalisation.get(fold(raw).replace(".", "").strip(), raw))
        if field.type == "zahl":
            value = value.replace(".", ",")  # German decimal separator
        confidence = note_confidence(note)
        if best is None or confidence > best[0]:
            best = (confidence, value, note)

    if best is None:
        return _empty(field, "kein Aufmass/Wert in den Notizen zu dieser Position gefunden")

    confidence, value, note = best
    return Cell(field_id=field.id, value=value, confidence=confidence, sources=[note.id], reason=_evidence(note))


def _from_keyword_sentence(field: Field, notes: list[Note]) -> Cell:
    """Take the one sentence that actually says something about this field."""
    keywords = [fold(k) for k in field.extraction.get("stichworte", [])]
    best: tuple[float, str, Note] | None = None

    for note in notes:
        confidence = note_confidence(note)
        for sentence in _SENTENCE_SPLIT.split(note.content):
            cleaned = sentence.strip().strip("-").strip()
            if not cleaned:
                continue
            if any(k in fold(cleaned) for k in keywords):
                if best is None or confidence > best[0]:
                    best = (confidence, cleaned.rstrip("."), note)
                break

    if best is None:
        return _empty(field, "keine passende Aussage in den Notizen zu dieser Position")

    confidence, value, note = best
    return Cell(field_id=field.id, value=value, confidence=confidence, sources=[note.id], reason=_evidence(note))


def _from_photos(field: Field, notes: list[Note]) -> Cell:
    photos = [n for n in notes if n.kind == "foto"]
    if not photos:
        return _empty(field, "kein Foto zu dieser Position vorhanden")
    labels = [str(n.meta.get("dateiname", n.id)) for n in photos]
    return Cell(
        field_id=field.id,
        value=", ".join(labels),
        confidence=SOURCE_CONFIDENCE["system"],
        sources=[n.id for n in photos],
        reason=f"{len(photos)} Foto(s) zur Position",
    )


def _from_header_pattern(field: Field, inspection: Inspection) -> Cell:
    """Header facts can be mentioned anywhere, so search every note."""
    pattern = re.compile(field.extraction["muster"], re.IGNORECASE)
    # Notes taken without a position are the ones about the appointment itself.
    ordered = sorted(inspection.notes, key=lambda n: (n.position is not None, -note_confidence(n)))
    for note in ordered:
        match = pattern.search(note.content)
        if not match:
            continue
        value = (match.group(1) or "").strip(" .,;-")
        if not value:
            continue
        return Cell(
            field_id=field.id,
            value=value,
            confidence=note_confidence(note),
            sources=[note.id],
            reason=_evidence(note),
        )
    return _empty(field, "in keiner Notiz erwaehnt")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _extract_cell(field: Field, inspection: Inspection, position: str | None, index: int) -> Cell:
    notes = inspection.notes_for(position) if position else []
    strategy = field.strategy

    if strategy == "laufende_nummer":
        return _running_number(field, index)
    if strategy == "metadaten":
        return _from_metadata(field, inspection)
    if strategy == "kopf_muster":
        return _from_header_pattern(field, inspection)
    if strategy == "positionsfeld":
        return _from_position(field, position or "")
    if strategy == "auswahl_synonyme":
        return _from_choice_synonyms(field, notes)
    if strategy == "muster":
        return _from_pattern(field, notes)
    if strategy == "stichwort_satz":
        return _from_keyword_sentence(field, notes)
    if strategy == "foto_referenz":
        return _from_photos(field, notes)
    return _empty(field, f"keine Extraktionsstrategie fuer '{field.id}' hinterlegt")


def extract_table(inspection: Inspection, schema: TableSchema) -> ProtocolTable:
    """Fill the customer table from one inspection export, cell by cell."""
    table = ProtocolTable(
        schema_id=schema.id,
        schema_version=schema.version,
        title=schema.title,
    )
    for field in schema.header_fields:
        table.header[field.id] = _extract_cell(field, inspection, None, 0)

    for index, position in enumerate(inspection.positions(), start=1):
        row = Row(position=position)
        for field in schema.position_fields:
            row.cells[field.id] = _extract_cell(field, inspection, position, index)
        table.rows.append(row)

    return table
