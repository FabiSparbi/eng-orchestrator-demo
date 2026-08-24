"""Tests for the KI-Protokoll flow.

The two behaviours worth guarding are the ones the feature is actually about:

  * an entry that cannot be derived reliably NEVER lands in the customer's
    table as if it were fine -- it becomes a to-do with a capture hint;
  * pressing the button again after adding material re-checks and says what
    the addition closed and what is still missing.

Everything runs offline against the rule extractor.
"""

from __future__ import annotations

import pytest

from ki_protokoll import store
from ki_protokoll.completeness import HEADER_POSITION, diff_open_items, find_open_items
from ki_protokoll.extraction import extract_table, note_confidence
from ki_protokoll.models import Note
from ki_protokoll.render import OPEN_MARKER, table_to_csv, table_to_markdown
from ki_protokoll.schema import load_schema
from ki_protokoll.service import ki_protokoll_button, ki_protokoll_erzeugen


@pytest.fixture(autouse=True)
def clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture()
def inspection():
    return store.load_inspection()


@pytest.fixture()
def schema():
    return load_schema()


# --- schema ---------------------------------------------------------------

def test_schema_defines_the_customer_columns(schema):
    position_ids = [f.id for f in schema.position_fields]
    assert position_ids == [
        "pos", "raum", "bauteil", "zustand", "feststellung",
        "menge", "einheit", "massnahme", "prioritaet", "foto",
    ]
    assert [f.id for f in schema.header_fields][0] == "objekt"
    assert schema.min_confidence == 0.7


# --- extraction -----------------------------------------------------------

def test_well_documented_position_is_fully_derived(inspection, schema):
    table = extract_table(inspection, schema)
    kitchen = next(r for r in table.rows if r.position.startswith("Kueche"))

    assert kitchen.value("raum") == "Kueche"
    assert kitchen.value("bauteil") == "Fenster"
    assert kitchen.value("zustand") == "beschaedigt"
    assert kitchen.value("menge") == "1,4"
    assert kitchen.value("einheit") == "lfdm"
    assert kitchen.value("prioritaet") == "kurzfristig"
    assert kitchen.value("foto") == "IMG_0431.jpg"


def test_nothing_is_invented_for_undocumented_cells(inspection, schema):
    table = extract_table(inspection, schema)
    bath = next(r for r in table.rows if r.position.startswith("Bad"))

    for field_id in ("menge", "einheit", "prioritaet", "foto"):
        cell = bath.cells[field_id]
        assert cell.value is None
        assert cell.confidence == 0.0
        assert cell.reason  # always says why it is empty


def test_two_notes_agreeing_raise_confidence(inspection, schema):
    table = extract_table(inspection, schema)
    kitchen = next(r for r in table.rows if r.position.startswith("Kueche"))
    cell = kitchen.cells["zustand"]

    assert len(cell.sources) == 2  # voice note plus photo
    assert cell.confidence > 0.85


def test_hedged_photo_evidence_stays_below_the_threshold(inspection, schema):
    """The photo says "wirkt lose ... vermutlich" -- that is not sign-off material."""
    table = extract_table(inspection, schema)
    stairs = next(r for r in table.rows if r.position.startswith("Treppenhaus"))
    cell = stairs.cells["zustand"]

    assert cell.value == "beschaedigt"
    assert cell.confidence < schema.min_confidence


def test_hedge_words_lower_note_confidence():
    plain = Note(id="A", kind="sprache", content="Die Fuge ist undicht.")
    hedged = Note(id="B", kind="sprache", content="Die Fuge ist vermutlich undicht.")
    assert note_confidence(hedged) < note_confidence(plain)


def test_contradicting_notes_become_a_conflict_cell(inspection, schema):
    store.add_notes(
        inspection.id,
        [Note(id="N-900", kind="text", content="Das Kuechenfenster ist neuwertig.", position="Kueche / Fenster")],
    )
    table = extract_table(inspection, schema)
    kitchen = next(r for r in table.rows if r.position.startswith("Kueche"))
    cell = kitchen.cells["zustand"]

    assert cell.conflict  # both readings kept
    assert cell.confidence <= 0.4
    open_items = find_open_items(table, schema)
    conflict = next(i for i in open_items if i.field_id == "zustand" and i.position.startswith("Kueche"))
    assert conflict.status == "widerspruch"
    assert conflict.capture == "Sprachnotiz"  # a photo cannot settle a contradiction


# --- completeness ---------------------------------------------------------

def test_open_items_name_position_column_and_capture_type(inspection, schema):
    table = extract_table(inspection, schema)
    items = find_open_items(table, schema)

    assert any(i.position == HEADER_POSITION and i.field_id == "auftragsnummer" for i in items)
    photo_todo = next(i for i in items if i.field_id == "foto")
    assert photo_todo.capture == "Foto"
    assert photo_todo.hint
    # System-filled columns are never handed to the user as a to-do.
    assert not any(i.field_id in {"pos", "objekt", "adresse", "besichtigungsdatum"} for i in items)


def test_diff_splits_resolved_still_open_and_new(inspection, schema):
    before = find_open_items(extract_table(inspection, schema), schema)
    store.add_notes(inspection.id, store.load_follow_up_notes())
    after = find_open_items(extract_table(inspection, schema), schema)

    resolved, still_open, new_items = diff_open_items(before, after)
    assert len(resolved) == len(before)
    assert still_open == [] and new_items == []


# --- the button -----------------------------------------------------------

def test_first_press_returns_table_and_todo_list(inspection):
    result = ki_protokoll_erzeugen(inspection.id)

    assert result.run == 1
    assert result.complete is False
    assert result.open_items
    assert result.notification.level == "hinweis"
    assert "KI-Protokoll" in result.notification.body
    assert result.notification.todos == result.open_items
    # The table is produced anyway, with the gaps visible.
    assert OPEN_MARKER in result.table_markdown
    assert result.prose is None  # prose is opt-in


def test_second_press_after_full_follow_up_completes_the_table(inspection):
    first = ki_protokoll_erzeugen(inspection.id)
    store.add_notes(inspection.id, store.load_follow_up_notes())
    second = ki_protokoll_erzeugen(inspection.id, mit_fliesstext=True)

    assert second.run == 2
    assert second.complete is True
    assert second.open_items == []
    assert len(second.resolved_items) == len(first.open_items)
    assert second.notification.level == "erfolg"
    assert OPEN_MARKER not in second.table_markdown
    assert second.prose and "Besichtigungsprotokoll" in second.prose


def test_second_press_after_partial_follow_up_reports_what_is_left(inspection):
    first = ki_protokoll_erzeugen(inspection.id)
    store.add_notes(
        inspection.id,
        [Note(id="N-101", kind="text", content="Auftragsnummer 2026-118.", position=None)],
    )
    second = ki_protokoll_erzeugen(inspection.id)

    assert second.complete is False
    assert [i.field_id for i in second.resolved_items] == ["auftragsnummer"]
    assert len(second.open_items) == len(first.open_items) - 1
    assert "1 Punkt(e) durch deine Ergaenzungen geschlossen." in second.notification.body
    # The remaining points are named again, per position.
    assert "Bad / Fliesenspiegel:" in second.notification.body


def test_repeated_press_without_new_material_changes_nothing(inspection):
    first = ki_protokoll_erzeugen(inspection.id)
    second = ki_protokoll_erzeugen(inspection.id)

    assert [i.key for i in second.open_items] == [i.key for i in first.open_items]
    assert second.resolved_items == []
    assert "noch keinen der offenen Punkte geschlossen" in second.notification.body


def test_adding_the_same_note_twice_is_a_no_op(inspection):
    follow_up = store.load_follow_up_notes()
    store.add_notes(inspection.id, follow_up)
    count = len(store.get_inspection(inspection.id).notes)
    store.add_notes(inspection.id, follow_up)
    assert len(store.get_inspection(inspection.id).notes) == count


def test_button_wrapper_is_json_serialisable(inspection):
    import json

    payload = ki_protokoll_button(inspection.id, mit_fliesstext=True)
    json.dumps(payload, ensure_ascii=False)  # must not raise

    assert payload["vollstaendig"] is False
    assert payload["benachrichtigung"]["todos"]
    assert payload["tabelle"]["positionen"][0]["zustand"]["quellen"]


# --- rendering ------------------------------------------------------------

def test_csv_carries_header_block_and_one_row_per_position(inspection, schema):
    table = extract_table(inspection, schema)
    csv_text = table_to_csv(table, schema)
    lines = [line for line in csv_text.splitlines() if line.strip()]

    assert lines[0].startswith("Objekt;")
    assert "Pos.;Raum / Bereich;Bauteil" in csv_text
    assert len([line for line in lines if line.startswith(("1;", "2;", "3;"))]) == len(table.rows)


def test_markdown_marks_uncertain_cells(inspection, schema):
    table = extract_table(inspection, schema)
    markdown = table_to_markdown(table, schema)

    stairs_line = next(line for line in markdown.splitlines() if line.startswith("| 3 |"))
    assert " ? " in stairs_line or stairs_line.endswith("? |")
