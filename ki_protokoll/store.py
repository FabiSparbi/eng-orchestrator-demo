"""The inspection export and the run history behind the button.

Two pieces of state, both in memory:

  * the inspection export -- the notes, photos and voice notes of one
    appointment, which the user keeps adding to between button presses;
  * the run history -- what was open the last time the button was pressed,
    which is what makes "these points are now closed" answerable.

=============================================================================
PERSISTENCE NOTE: like `tools/loop_state.py`, this is a module-level dict that
dies with the process. Swap `_INSPECTIONS` / `_RUNS` for the app's own storage
behind these same functions; nothing above this module knows the difference.
=============================================================================
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ki_protokoll.models import Inspection, Note, OpenItem

MOCK_DIR = Path(__file__).resolve().parent / "mock_data"
DEFAULT_EXPORT = MOCK_DIR / "besichtigung_export.json"
FOLLOW_UP_NOTES = MOCK_DIR / "nachtrag_notizen.json"

_INSPECTIONS: dict[str, Inspection] = {}
_RUNS: dict[str, list[list[OpenItem]]] = {}


def load_inspection(path: Path | str = DEFAULT_EXPORT) -> Inspection:
    """Load an inspection export from disk and register it."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    inspection = Inspection.from_dict(raw)
    _INSPECTIONS[inspection.id] = inspection
    return inspection


def get_inspection(inspection_id: str) -> Inspection:
    inspection = _INSPECTIONS.get(inspection_id)
    if inspection is None:
        raise KeyError(
            f"Keine Besichtigung '{inspection_id}' geladen. Zuerst load_inspection(...) aufrufen."
        )
    return inspection


def add_notes(inspection_id: str, notes: list[Note] | list[dict[str, Any]]) -> Inspection:
    """Append what the user captured after seeing the to-do list.

    Duplicate note ids are ignored rather than appended twice -- re-sending the
    same follow-up must not change the outcome.
    """
    inspection = get_inspection(inspection_id)
    known = {n.id for n in inspection.notes}
    for entry in notes:
        note = entry if isinstance(entry, Note) else Note.from_dict(entry)
        if note.id in known:
            continue
        inspection.notes.append(note)
        known.add(note.id)
    return inspection


def load_follow_up_notes(path: Path | str = FOLLOW_UP_NOTES) -> list[Note]:
    """The mocked second round of captures (what the user adds after run 1)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Note.from_dict(n) for n in raw["notizen"]]


def record_run(inspection_id: str, open_items: list[OpenItem]) -> int:
    """Store what was open after a run; returns the 1-based run number."""
    runs = _RUNS.setdefault(inspection_id, [])
    runs.append(list(open_items))
    return len(runs)


def previous_open_items(inspection_id: str) -> list[OpenItem]:
    runs = _RUNS.get(inspection_id, [])
    return list(runs[-1]) if runs else []


def run_count(inspection_id: str) -> int:
    return len(_RUNS.get(inspection_id, []))


def snapshot(inspection_id: str) -> dict[str, Any]:
    """A copy of the export as it stands -- handy for debugging a run."""
    return deepcopy(get_inspection(inspection_id).to_dict())


def reset() -> None:
    """Clear all inspections and run history (used by tests and the demo)."""
    _INSPECTIONS.clear()
    _RUNS.clear()
