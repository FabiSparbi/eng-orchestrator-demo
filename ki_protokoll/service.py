"""The KI-Protokoll button.

One entry point, pressed as often as the user likes:

    extract -> check completeness -> diff against the last run -> notify

The same call handles the first press and every later one. There is no
"generate" mode and no separate "check" mode, because the user does not think
in modes: they press the button, add what was missing, and press it again.
What changes between runs is only the material in the inspection export.

The table is always produced -- also when entries are still open. A protocol
with visible gaps is more useful on site than no protocol at all, and the gaps
are exactly what the notification is about. The prose report is optional
(`mit_fliesstext`) because the customer's format is the table.
"""

from __future__ import annotations

from typing import Any, Callable

from ki_protokoll import store
from ki_protokoll.completeness import diff_open_items, find_open_items
from ki_protokoll.extraction import extract_table
from ki_protokoll.models import Inspection, ProtocolResult, ProtocolTable
from ki_protokoll.notification import build_notification
from ki_protokoll.render import render_prose, table_to_csv, table_to_markdown
from ki_protokoll.schema import DEFAULT_SCHEMA, TableSchema, load_schema

Extractor = Callable[[Inspection, TableSchema], ProtocolTable]


def ki_protokoll_erzeugen(
    besichtigung_id: str,
    schema_name: str = DEFAULT_SCHEMA,
    mit_fliesstext: bool = False,
    extractor: Extractor | None = None,
) -> ProtocolResult:
    """Run the KI-Protokoll step for one inspection.

    Args:
        besichtigung_id: Id of the loaded inspection export.
        schema_name: Which customer table format to fill. Default is the one
            shipped in `ki_protokoll/schemas/`.
        mit_fliesstext: Also produce the prose protocol. Off by default -- the
            table is the deliverable, the text is nice to have.
        extractor: Override the extraction step (rule-based by default; pass
            `ki_protokoll.llm.extract_table_with_llm` for the model path).

    Returns:
        The filled table, the open entries, and the notification for the user.
    """
    schema = load_schema(schema_name)
    inspection = store.get_inspection(besichtigung_id)

    table = (extractor or extract_table)(inspection, schema)
    open_items = find_open_items(table, schema)

    previous = store.previous_open_items(besichtigung_id)
    is_repeat = store.run_count(besichtigung_id) > 0
    resolved, _still_open, new_items = diff_open_items(previous, open_items)
    run = store.record_run(besichtigung_id, open_items)

    notification = build_notification(
        run=run,
        open_items=open_items,
        resolved=resolved if is_repeat else [],
        new_items=new_items if is_repeat else [],
        row_count=len(table.rows),
    )

    return ProtocolResult(
        inspection_id=besichtigung_id,
        run=run,
        complete=not open_items,
        table=table,
        table_markdown=table_to_markdown(table, schema),
        table_csv=table_to_csv(table, schema),
        open_items=open_items,
        resolved_items=resolved if is_repeat else [],
        new_items=new_items if is_repeat else [],
        notification=notification,
        prose=render_prose(table, schema) if mit_fliesstext else None,
    )


def ki_protokoll_button(
    besichtigung_id: str,
    mit_fliesstext: bool = False,
    schema_name: str = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """JSON-shaped wrapper, for wiring the button to an API or an agent tool.

    Args:
        besichtigung_id: Id of the loaded inspection export.
        mit_fliesstext: Also return the optional prose protocol.
        schema_name: Which customer table format to fill.
    """
    return ki_protokoll_erzeugen(
        besichtigung_id, schema_name=schema_name, mit_fliesstext=mit_fliesstext
    ).to_dict()
