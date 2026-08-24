"""The message the user gets after pressing "KI-Protokoll".

Two jobs, and the second is the one that matters:

1. say whether the table is complete;
2. if it is not, hand over a to-do list the user can work off INSIDE the
   inspection export -- per position, per column, with the capture type that
   would close it.

From the second run on, the message also reports what the added material
actually closed. Without that, pressing the button again feels like it did
nothing; with it, the user sees the list shrink.
"""

from __future__ import annotations

from collections import OrderedDict

from ki_protokoll.completeness import HEADER_POSITION
from ki_protokoll.models import Notification, OpenItem

_STATUS_TEXT = {
    "fehlt": "fehlt",
    "unsicher": "unsicher",
    "widerspruch": "widerspruechlich",
}


def group_by_position(items: list[OpenItem]) -> "OrderedDict[str, list[OpenItem]]":
    grouped: OrderedDict[str, list[OpenItem]] = OrderedDict()
    for item in sorted(items, key=lambda i: (i.position != HEADER_POSITION, i.position)):
        grouped.setdefault(item.position, []).append(item)
    return grouped


def todo_lines(items: list[OpenItem]) -> list[str]:
    """The to-do list as the app would render it, one line per open entry."""
    lines: list[str] = []
    for position, group in group_by_position(items).items():
        lines.append(f"{position}:")
        for item in group:
            lines.append(
                f"  [ ] {item.label} ({_STATUS_TEXT.get(item.status, item.status)}) "
                f"-> {item.capture}: {item.hint}"
            )
    return lines


def build_notification(
    run: int,
    open_items: list[OpenItem],
    resolved: list[OpenItem],
    new_items: list[OpenItem],
    row_count: int,
) -> Notification:
    if not open_items:
        body = [
            f"Alle Pflichtangaben der {row_count} Positionen konnten aus Notizen, Fotos und "
            "Sprachnotizen abgeleitet werden.",
            "Die Tabelle im Kundenformat steht zum Export bereit.",
        ]
        if resolved:
            body.insert(0, f"Deine Ergaenzungen haben die letzten {len(resolved)} offenen Punkte geschlossen.")
        return Notification(
            level="erfolg",
            title="Protokoll vollstaendig",
            body="\n".join(body),
            todos=[],
            resolved=resolved,
        )

    body: list[str] = []
    if run > 1 and resolved:
        body.append(f"{len(resolved)} Punkt(e) durch deine Ergaenzungen geschlossen.")
    if run > 1 and not resolved:
        body.append("Die Ergaenzungen haben noch keinen der offenen Punkte geschlossen.")
    if new_items and run > 1:
        body.append(f"{len(new_items)} Punkt(e) sind neu hinzugekommen.")

    body.append(
        f"{len(open_items)} Pflichtangabe(n) konnten nicht zuverlaessig abgeleitet werden. "
        "Bitte im Besichtigungs-Export ergaenzen und danach erneut auf \"KI-Protokoll\" tippen."
    )
    body.append("")
    body.extend(todo_lines(open_items))

    return Notification(
        level="hinweis",
        title=f"Protokoll erzeugt - {len(open_items)} Angabe(n) offen",
        body="\n".join(body),
        todos=open_items,
        resolved=resolved,
    )
