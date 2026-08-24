"""The customer's table format, loaded from JSON.

The target format is the customer's, not ours, and it will change. So the
columns, which of them are mandatory, the allowed values, how each one is
derived and what the user should capture to fill it all live in
`schemas/*.json` -- never in the extractor and never in the renderer. Swapping
in a different customer format is a data change, not a code change.

A field description carries three things at once:

  * the OUTPUT contract   -- label, type, allowed values, mandatory yes/no
  * the EXTRACTION recipe -- which strategy derives it from the captured notes
  * the CAPTURE hint      -- what the user should record if it could not be
                             derived, and as what (photo / voice / text)

The third one is the reason the to-do list can be specific instead of just
saying "incomplete".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
DEFAULT_SCHEMA = "kunde_besichtigung_v1"

# A cell below this confidence is reported as "unsicher" rather than silently
# written into the customer's table. Per-field overrides win over the schema
# default, which wins over this.
FALLBACK_MIN_CONFIDENCE = 0.7


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    mandatory: bool
    type: str
    hint: str
    capture: str
    extraction: dict[str, Any]
    allowed: list[str] = field(default_factory=list)
    min_confidence: float | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Field:
        return cls(
            id=raw["id"],
            label=raw["label"],
            mandatory=bool(raw.get("pflicht", False)),
            type=raw.get("typ", "text"),
            hint=raw.get("erfassungshinweis", ""),
            capture=raw.get("empfohleneErfassung", "Textnotiz"),
            extraction=raw.get("extraktion", {}),
            allowed=list(raw.get("erlaubteWerte", [])),
            min_confidence=raw.get("minKonfidenz"),
        )

    @property
    def strategy(self) -> str:
        return self.extraction.get("strategie", "none")

    @property
    def system_generated(self) -> bool:
        """True for columns the app fills itself -- never a to-do for the user."""
        return self.strategy in {"laufende_nummer", "metadaten"}


@dataclass(frozen=True)
class TableSchema:
    id: str
    version: str
    title: str
    customer: str
    min_confidence: float
    header_fields: list[Field]
    position_fields: list[Field]

    def threshold(self, f: Field) -> float:
        return f.min_confidence if f.min_confidence is not None else self.min_confidence

    def field_by_id(self, field_id: str) -> Field | None:
        for f in (*self.header_fields, *self.position_fields):
            if f.id == field_id:
                return f
        return None


def load_schema(name: str = DEFAULT_SCHEMA) -> TableSchema:
    """Load a customer table format by file stem (or by path)."""
    path = Path(name) if str(name).endswith(".json") else SCHEMA_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in SCHEMA_DIR.glob("*.json")))
        raise FileNotFoundError(f"Tabellenschema '{name}' nicht gefunden. Vorhanden: {available}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    return TableSchema(
        id=raw["schemaId"],
        version=str(raw.get("version", "1.0")),
        title=raw.get("titel", "Protokoll"),
        customer=raw.get("kunde", ""),
        min_confidence=float(raw.get("minKonfidenz", FALLBACK_MIN_CONFIDENCE)),
        header_fields=[Field.from_dict(f) for f in raw.get("kopf", [])],
        position_fields=[Field.from_dict(f) for f in raw.get("positionen", [])],
    )
