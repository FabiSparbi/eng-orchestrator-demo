"""Data types for the KI-Protokoll flow.

Two ideas carry the whole feature and both live here:

CELL-LEVEL CONFIDENCE
    Every cell of the customer table knows how it came to be: a value, a
    confidence between 0 and 1, and the ids of the notes it was derived from.
    Without that per-cell record there is no honest way to say which entries
    could NOT be derived reliably -- which is exactly what the user has to be
    told after pressing the button.

OPEN ITEMS ARE ADDRESSED, NOT JUST LISTED
    An `OpenItem` names the position and the field, and says which kind of
    capture (photo / voice note / text note) would close it. That is what turns
    "the table is incomplete" into a to-do list the user can actually work off
    inside the inspection export.

Identifiers are English (repo convention); everything user-facing is German,
because the table goes to the customer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NoteKind = Literal["text", "sprache", "foto"]
OpenStatus = Literal["fehlt", "unsicher", "widerspruch"]

# What the app captures in an inspection step, and how much a value derived
# from it is worth. A transcript is a little less reliable than typed text; a
# value read out of an image description is markedly less reliable and, at
# 0.68, lands below the default 0.7 threshold on its own -- photo-only evidence
# gets confirmed by the user rather than silently written into the protocol.
SOURCE_CONFIDENCE: dict[str, float] = {
    "text": 0.92,
    "sprache": 0.85,
    "foto": 0.68,
    "system": 1.0,
}

KIND_LABEL: dict[str, str] = {
    "text": "Textnotiz",
    "sprache": "Sprachnotiz",
    "foto": "Foto",
    "system": "App-Stammdaten",
}


@dataclass(frozen=True)
class Note:
    """One captured item from an inspection step.

    `content` is always text: what the user typed, the transcript of the voice
    note, or the description a vision model produced for the photo. Keeping all
    three in one shape is what lets the extractor treat them uniformly and still
    weight them differently (see SOURCE_CONFIDENCE).
    """

    id: str
    kind: NoteKind
    content: str
    position: str | None = None  # "Bad / Fliesenspiegel"; None = Kopfdaten
    captured_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Note:
        return cls(
            id=raw["id"],
            kind=raw["art"],
            content=raw.get("inhalt", ""),
            position=raw.get("position"),
            captured_at=raw.get("zeitpunkt"),
            meta=raw.get("meta", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "art": self.kind,
            "inhalt": self.content,
            "position": self.position,
            "zeitpunkt": self.captured_at,
            "meta": self.meta,
        }


@dataclass
class Inspection:
    """The inspection export: master data plus everything captured on site."""

    id: str
    metadata: dict[str, Any]
    notes: list[Note] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Inspection:
        return cls(
            id=raw["besichtigungId"],
            metadata=raw.get("stammdaten", {}),
            notes=[Note.from_dict(n) for n in raw.get("notizen", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "besichtigungId": self.id,
            "stammdaten": self.metadata,
            "notizen": [n.to_dict() for n in self.notes],
        }

    def positions(self) -> list[str]:
        """Distinct positions, in the order they were first captured."""
        seen: list[str] = []
        for note in self.notes:
            if note.position and note.position not in seen:
                seen.append(note.position)
        return seen

    def notes_for(self, position: str | None) -> list[Note]:
        return [n for n in self.notes if n.position == position]


@dataclass
class Cell:
    """One table cell plus the evidence behind it."""

    field_id: str
    value: str | None = None
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)  # note ids
    reason: str = ""
    conflict: list[str] = field(default_factory=list)  # competing values, if any

    @property
    def filled(self) -> bool:
        return bool(self.value is not None and str(self.value).strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "wert": self.value,
            "konfidenz": round(self.confidence, 2),
            "quellen": list(self.sources),
            "begruendung": self.reason,
            "konflikt": list(self.conflict),
        }


@dataclass
class Row:
    """One position row of the customer table."""

    position: str
    cells: dict[str, Cell] = field(default_factory=dict)

    def value(self, field_id: str) -> str | None:
        cell = self.cells.get(field_id)
        return cell.value if cell else None


@dataclass
class ProtocolTable:
    """The customer table: a header block and the position rows."""

    schema_id: str
    schema_version: str
    title: str
    header: dict[str, Cell] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{self.schema_id}@{self.schema_version}",
            "titel": self.title,
            "kopf": {fid: cell.to_dict() for fid, cell in self.header.items()},
            "positionen": [
                {"position": row.position, **{fid: cell.to_dict() for fid, cell in row.cells.items()}}
                for row in self.rows
            ],
        }


@dataclass(frozen=True)
class OpenItem:
    """One entry of the to-do list handed back to the user."""

    position: str  # "Kopfdaten" for the header block
    field_id: str
    label: str
    status: OpenStatus
    reason: str
    hint: str
    capture: str  # "Foto" | "Sprachnotiz" | "Textnotiz"

    @property
    def key(self) -> tuple[str, str]:
        """Identity across runs -- this is what makes 'still open' decidable."""
        return (self.position, self.field_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "feld": self.field_id,
            "spalte": self.label,
            "status": self.status,
            "grund": self.reason,
            "hinweis": self.hint,
            "erfassung": self.capture,
        }


@dataclass
class Notification:
    """What the app shows after the KI-Protokoll button was pressed."""

    level: Literal["erfolg", "hinweis"]
    title: str
    body: str
    todos: list[OpenItem] = field(default_factory=list)
    resolved: list[OpenItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stufe": self.level,
            "titel": self.title,
            "text": self.body,
            "todos": [t.to_dict() for t in self.todos],
            "seitLetztemLaufGeloest": [r.to_dict() for r in self.resolved],
        }


@dataclass
class ProtocolResult:
    """Everything one press of the KI-Protokoll button produces."""

    inspection_id: str
    run: int
    complete: bool
    table: ProtocolTable
    table_markdown: str
    table_csv: str
    open_items: list[OpenItem]
    resolved_items: list[OpenItem]
    new_items: list[OpenItem]
    notification: Notification
    prose: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "besichtigungId": self.inspection_id,
            "lauf": self.run,
            "vollstaendig": self.complete,
            "tabelle": self.table.to_dict(),
            "tabelleMarkdown": self.table_markdown,
            "tabelleCsv": self.table_csv,
            "offenePunkte": [i.to_dict() for i in self.open_items],
            "seitLetztemLaufGeloest": [i.to_dict() for i in self.resolved_items],
            "neuOffen": [i.to_dict() for i in self.new_items],
            "benachrichtigung": self.notification.to_dict(),
            "fliesstext": self.prose,
        }
