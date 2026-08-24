"""Which entries could NOT be derived reliably -- and what would fix them.

The check is deliberately boring and deterministic: a mandatory cell is open if
it is empty, if its confidence is under the threshold, or if the notes
contradicted each other. Three statuses, because they need three different
sentences to the user:

    fehlt        nothing in the material says anything about this
    unsicher     something does, but not clearly enough to sign off on
    widerspruch  two notes say different things; a human has to decide

System-generated columns (running number, master data) are never a to-do:
telling the user to go photograph the position number would be nonsense.
"""

from __future__ import annotations

from ki_protokoll.models import Cell, OpenItem, ProtocolTable
from ki_protokoll.schema import Field, TableSchema

HEADER_POSITION = "Kopfdaten"

# Sort order for the to-do list: contradictions first (a human decision is
# blocking), then outright gaps, then soft ones.
_STATUS_RANK = {"widerspruch": 0, "fehlt": 1, "unsicher": 2}


def _judge(cell: Cell | None, field: Field, schema: TableSchema) -> tuple[str, str] | None:
    """Return (status, reason) if this cell is open, else None."""
    threshold = schema.threshold(field)

    if cell is None or not cell.filled:
        detail = cell.reason if cell and cell.reason else "kein Hinweis im Material"
        return "fehlt", detail

    if cell.conflict:
        return "widerspruch", cell.reason or "widerspruechliche Angaben im Material"

    if cell.confidence < threshold:
        return (
            "unsicher",
            f"nur mit Konfidenz {cell.confidence:.2f} abgeleitet ({cell.reason}); "
            f"erforderlich sind {threshold:.2f}",
        )
    return None


def _capture_advice(field: Field, status: str) -> str:
    """A contradiction is never resolved by another photo -- ask for a decision."""
    if status == "widerspruch":
        return "Sprachnotiz"
    return field.capture


def find_open_items(table: ProtocolTable, schema: TableSchema) -> list[OpenItem]:
    """Every mandatory cell that a person still has to close, header and rows."""
    items: list[OpenItem] = []

    for field in schema.header_fields:
        if not field.mandatory or field.system_generated:
            continue
        verdict = _judge(table.header.get(field.id), field, schema)
        if verdict:
            status, reason = verdict
            items.append(
                OpenItem(
                    position=HEADER_POSITION,
                    field_id=field.id,
                    label=field.label,
                    status=status,  # type: ignore[arg-type]
                    reason=reason,
                    hint=field.hint,
                    capture=_capture_advice(field, status),
                )
            )

    for row in table.rows:
        for field in schema.position_fields:
            if not field.mandatory or field.system_generated:
                continue
            verdict = _judge(row.cells.get(field.id), field, schema)
            if verdict:
                status, reason = verdict
                items.append(
                    OpenItem(
                        position=row.position,
                        field_id=field.id,
                        label=field.label,
                        status=status,  # type: ignore[arg-type]
                        reason=reason,
                        hint=field.hint,
                        capture=_capture_advice(field, status),
                    )
                )

    items.sort(key=lambda i: (_STATUS_RANK.get(i.status, 9), i.position != HEADER_POSITION, i.position))
    return items


def diff_open_items(
    previous: list[OpenItem], current: list[OpenItem]
) -> tuple[list[OpenItem], list[OpenItem], list[OpenItem]]:
    """Compare two runs: (resolved, still open, newly open).

    This is what makes pressing the button a second time meaningful -- the user
    gets told what their added material actually closed, not just a fresh list.
    """
    previous_keys = {i.key: i for i in previous}
    current_keys = {i.key: i for i in current}

    resolved = [i for key, i in previous_keys.items() if key not in current_keys]
    still_open = [i for key, i in current_keys.items() if key in previous_keys]
    newly_open = [i for key, i in current_keys.items() if key not in previous_keys]
    return resolved, still_open, newly_open
