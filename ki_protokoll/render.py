"""Rendering the table (the deliverable) and the prose report (nice to have).

The table is the customer's target format, so it is rendered from the schema's
column order -- Markdown for the app's preview, CSV (semicolon, so German Excel
opens it straight) for the export.

Cells that are open are never rendered as if they were fine. An empty mandatory
cell shows as `-- offen --`, a below-threshold one keeps a `?` marker. A
protocol that hides its own gaps is the failure mode this whole feature exists
to prevent.

The prose report is deliberately generated FROM the finished table, not from
the notes: table and text can then never tell different stories. It is optional
on purpose (`mit_fliesstext=True`) -- the table is what the customer wants.
"""

from __future__ import annotations

import csv
import io

from ki_protokoll.models import Cell, ProtocolTable
from ki_protokoll.schema import Field, TableSchema

OPEN_MARKER = "-- offen --"
UNCERTAIN_MARKER = "?"


def _display(cell: Cell | None, field: Field, schema: TableSchema) -> str:
    if cell is None or not cell.filled:
        return OPEN_MARKER if field.mandatory else ""
    if cell.conflict:
        return f"{cell.value} {UNCERTAIN_MARKER} (Widerspruch: {' / '.join(cell.conflict)})"
    if cell.confidence < schema.threshold(field):
        return f"{cell.value} {UNCERTAIN_MARKER}"
    return str(cell.value)


def _raw(cell: Cell | None) -> str:
    return "" if cell is None or not cell.filled else str(cell.value)


def table_to_markdown(table: ProtocolTable, schema: TableSchema) -> str:
    """The customer table as Markdown, gaps and uncertainties visible."""
    lines = [f"### {table.title}", ""]

    for field in schema.header_fields:
        lines.append(f"**{field.label}:** {_display(table.header.get(field.id), field, schema)}")
    lines.append("")

    lines.append("| " + " | ".join(f.label for f in schema.position_fields) + " |")
    lines.append("|" + "|".join("---" for _ in schema.position_fields) + "|")
    for row in table.rows:
        lines.append(
            "| " + " | ".join(_display(row.cells.get(f.id), f, schema) for f in schema.position_fields) + " |"
        )

    # The legend only appears when there is something to explain -- a clean
    # table should not carry a footnote about gaps it does not have.
    body = "\n".join(lines)
    if OPEN_MARKER in body or UNCERTAIN_MARKER in body:
        body += (
            f"\n\n_{UNCERTAIN_MARKER} = unsicher abgeleitet, bitte pruefen. "
            f"{OPEN_MARKER} = noch zu ergaenzen._"
        )
    return body


def table_to_csv(table: ProtocolTable, schema: TableSchema) -> str:
    """The same table as semicolon CSV, for the customer's export."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")

    for field in schema.header_fields:
        writer.writerow([field.label, _raw(table.header.get(field.id))])
    writer.writerow([])

    writer.writerow([f.label for f in schema.position_fields])
    for row in table.rows:
        writer.writerow([_raw(row.cells.get(f.id)) for f in schema.position_fields])

    return buffer.getvalue()


def render_prose(table: ProtocolTable, schema: TableSchema) -> str:
    """The optional Fliesstext protocol, generated from the finished table.

    Deterministic by design: no model call, no invented content, and it says
    plainly where the table is still open instead of writing around the gap.
    `ki_protokoll.llm.generate_prose` is the drop-in that produces a nicer text
    once a Foundry deployment is configured.
    """
    header = {f.id: _raw(table.header.get(f.id)) or "unbekannt" for f in schema.header_fields}
    parts = [
        f"{table.title}",
        "",
        (
            f"Am {header.get('besichtigungsdatum', 'unbekannt')} wurde das Objekt "
            f"\"{header.get('objekt', 'unbekannt')}\" ({header.get('adresse', 'Adresse unbekannt')}) besichtigt. "
            f"Teilnehmer: {header.get('teilnehmer', 'nicht dokumentiert')}. "
            f"Auftragsnummer: {header.get('auftragsnummer', 'nicht dokumentiert')}."
        ),
        "",
    ]

    if not table.rows:
        parts.append("Es wurden keine Positionen aufgenommen.")
        return "\n".join(parts)

    parts.append(f"Aufgenommen wurden {len(table.rows)} Positionen:")
    parts.append("")

    for row in table.rows:
        raum = row.value("raum") or "unbekannter Bereich"
        bauteil = row.value("bauteil") or "unbekanntes Bauteil"
        sentence = [f"{raum}, {bauteil}:"]

        zustand = row.value("zustand")
        feststellung = row.value("feststellung")
        if zustand and feststellung:
            sentence.append(f"Der Zustand ist {zustand}. {feststellung}.")
        elif zustand:
            sentence.append(f"Der Zustand ist {zustand}. Eine konkrete Feststellung liegt noch nicht vor.")
        elif feststellung:
            sentence.append(f"{feststellung}. Eine Zustandseinstufung steht noch aus.")
        else:
            sentence.append("Zustand und Feststellung sind noch nicht dokumentiert.")

        menge, einheit = row.value("menge"), row.value("einheit")
        if menge and einheit:
            sentence.append(f"Aufmass: {menge} {einheit}.")
        else:
            sentence.append("Ein Aufmass fehlt noch.")

        massnahme, prio = row.value("massnahme"), row.value("prioritaet")
        if massnahme:
            sentence.append(f"Empfohlene Massnahme: {massnahme}" + (f" (Prioritaet {prio})." if prio else "."))
        else:
            sentence.append("Eine Massnahme wurde noch nicht festgelegt.")

        foto = row.value("foto")
        sentence.append(f"Fotodokumentation: {foto}." if foto else "Es liegt noch kein Foto vor.")
        parts.append("- " + " ".join(sentence))

    parts.append("")
    parts.append(
        "Dieser Fliesstext ist aus der Protokolltabelle erzeugt und ersetzt sie nicht. "
        "Verbindlich ist die Tabelle im Kundenformat."
    )
    return "\n".join(parts)
