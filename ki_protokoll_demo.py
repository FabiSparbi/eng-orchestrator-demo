"""The KI-Protokoll button, end to end, offline.

    python ki_protokoll_demo.py                 # table + to-do list + second pass
    python ki_protokoll_demo.py --fliesstext    # additionally the prose protocol

No Azure, no credentials, no network: the rule-based extractor runs the same
flow the model-backed one will (`ki_protokoll/llm.py`), so the mechanics --
table, gaps, notification, second pass -- can be judged today.

What you see:

    DURCHGANG 1  the material from the appointment is incomplete. The table is
                 produced anyway, with its gaps marked, and the user gets a
                 to-do list per position.
    ERGAENZUNG   the user records what the list asked for.
    DURCHGANG 2  the same button again: what the additions closed, what would
                 still be missing, and the finished table.
"""

from __future__ import annotations

import argparse

from ki_protokoll import store
from ki_protokoll.service import ki_protokoll_erzeugen


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def show(result) -> None:  # noqa: ANN001 - ProtocolResult, kept loose for readability
    note = result.notification
    print(f"\n--- BENACHRICHTIGUNG ({note.level}) " + "-" * 42)
    print(f"{note.title}\n")
    print(note.body)
    print("-" * 78)
    print("\n" + result.table_markdown)


def main() -> None:
    parser = argparse.ArgumentParser(description="KI-Protokoll: Tabelle im Kundenformat aus Besichtigungsmaterial.")
    parser.add_argument("--fliesstext", action="store_true", help="Zusaetzlich das Fliesstext-Protokoll erzeugen.")
    parser.add_argument("--csv", action="store_true", help="Die Tabelle zusaetzlich als CSV ausgeben.")
    args = parser.parse_args()

    store.reset()
    inspection = store.load_inspection()

    rule("AUSGANGSLAGE  Besichtigungs-Export")
    print(f"Besichtigung : {inspection.id}")
    print(f"Objekt       : {inspection.metadata.get('objekt')}")
    print(f"Positionen   : {', '.join(inspection.positions())}")
    print(f"Aufnahmen    : {len(inspection.notes)}")
    for note in inspection.notes:
        target = note.position or "Kopfdaten"
        print(f"  {note.id}  {note.kind_label:<12} {target:<24} {note.content[:52]}...")

    rule("DURCHGANG 1  Nutzer drueckt \"KI-Protokoll\"")
    first = ki_protokoll_erzeugen(inspection.id, mit_fliesstext=args.fliesstext)
    show(first)
    print(f"\nVollstaendig: {first.complete}   offene Pflichtangaben: {len(first.open_items)}")

    rule("ERGAENZUNG  Nutzer nimmt die offenen Punkte im Besichtigungs-Export auf")
    follow_up = store.load_follow_up_notes()
    for note in follow_up:
        target = note.position or "Kopfdaten"
        print(f"  + {note.id}  {note.kind_label:<12} {target:<24} {note.content[:52]}...")
    store.add_notes(inspection.id, follow_up)

    rule("DURCHGANG 2  Nutzer drueckt erneut \"KI-Protokoll\"")
    second = ki_protokoll_erzeugen(inspection.id, mit_fliesstext=args.fliesstext)
    show(second)
    print(f"\nGeschlossen seit Lauf 1: {len(second.resolved_items)}")
    for item in second.resolved_items:
        print(f"  erledigt: {item.position} -> {item.label}")
    print(f"Weiterhin offen: {len(second.open_items)}")
    for item in second.open_items:
        print(f"  offen: {item.position} -> {item.label} ({item.status})")

    if args.csv:
        rule("EXPORT  Tabelle als CSV (Semikolon, oeffnet direkt in Excel)")
        print(second.table_csv)

    if args.fliesstext:
        rule("OPTIONAL  Fliesstext-Protokoll (nice to have, aus der Tabelle erzeugt)")
        print(second.prose)

    rule("ERGEBNIS")
    print(f"Tabelle vollstaendig: {second.complete}")
    print("Alle Inhalte sind synthetisch. Das Tabellenschema in")
    print("ki_protokoll/schemas/kunde_besichtigung_v1.json ist ein Platzhalter fuer das echte Kundenformat.")


if __name__ == "__main__":
    main()
