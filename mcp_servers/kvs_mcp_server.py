"""Minimal stdio MCP server exposing the mock KVS (PLM) system.

This is the demo's MCP integration point: the Part Search Agent does not import
this code, it launches this script as a subprocess and speaks MCP to it.

KVS holds part master data and drawing documents. Coarse search is deliberately
attribute-only -- component class, weight, creation date, vehicle model. Nothing
about a part's FEATURES is available here; establishing whether a part has a
soft foot requires processing its drawing, which is the fine-search stage.

PART NUMBER FORMAT
------------------
    <vehicleModel>.<componentCode>.<partId>      e.g. 10A.507.109

The middle segment is the component class; 507 is the B-pillar. Coarse search
filters on that segment, which is why a light, recent A-pillar or door inner
never appears in B-pillar results.

PRODUCTION SWAP-IN: point `_load_catalog` and `fetch_drawing_ocr` at the real
KVS query and document APIs. The agent side does not change.

Run standalone for a sanity check:  python mcp_servers/kvs_mcp_server.py
(it waits for MCP traffic on stdin -- Ctrl-C to exit)
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "mock_data" / "kvs_catalog.json"

# The drawing store lives in the repo, so the server can serve drawing OCR text
# the same way the real KVS serves drawing documents.
sys.path.insert(0, str(REPO_ROOT))
from mock_data.drawing_ocr import get_drawing_ocr  # noqa: E402

B_PILLAR_CODE = "507"

# Accepted spellings for the component filter -> component code.
COMPONENT_ALIASES = {
    "b-pillar": B_PILLAR_CODE,
    "b pillar": B_PILLAR_CODE,
    "bpillar": B_PILLAR_CODE,
    "507": B_PILLAR_CODE,
    "a-pillar": "501",
    "501": "501",
    "c-pillar": "503",
    "503": "503",
    "side sill": "504",
    "504": "504",
    "door inner": "813",
    "813": "813",
    "roof rail": "809",
    "809": "809",
}

mcp = FastMCP(
    "kvs",
    instructions=(
        "Mock KVS PLM system. Coarse attribute search over the part catalog "
        "(component class, weight, creation date, vehicle model) and retrieval "
        "of drawing documents. No feature data -- use drawing analysis for that."
    ),
)


def _load_catalog() -> list[dict[str, Any]]:
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["parts"]


def _component_code_of(part_number: str) -> str | None:
    """Extract the component class from the middle segment of a part number."""
    segments = part_number.split(".")
    return segments[1] if len(segments) == 3 else None


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (ValueError, TypeError):
        return None


@mcp.tool()
def search_kvs_coarse(
    component: str | None = "B-pillar",
    max_weight_kg: float | None = None,
    created_within_years: float | None = None,
    created_after: str | None = None,
    vehicle_model: str | None = None,
) -> dict[str, Any]:
    """Coarse attribute search over the KVS part catalog.

    Filters on component class (from the part number's middle segment), weight,
    creation date and vehicle model ONLY. KVS holds no feature information at
    this stage -- whether a part has a soft foot can only be established by
    analysing its drawing afterwards.

    Args:
        component: Component class to match, e.g. "B-pillar" (or the code
            "507"). Defaults to B-pillar. Pass None to search all components.
        max_weight_kg: Return only parts at or below this mass in kilograms.
        created_within_years: Return only parts created within this many years
            from today, e.g. 2 for "created in the last 2 years". Prefer this
            over `created_after` for relative requests.
        created_after: ISO date (YYYY-MM-DD) lower bound on creation date. Use
            only when the engineer gives an explicit date.
        vehicle_model: Optional vehicle model code to match, e.g. "10A".

    Returns:
        The applied filters, a match count, and the matching parts with their
        catalog metadata.
    """
    parts = _load_catalog()

    wanted_code: str | None = None
    if component:
        wanted_code = COMPONENT_ALIASES.get(component.strip().lower())
        if wanted_code is None:
            return {
                "error": f"Unknown component '{component}'.",
                "knownComponents": sorted({k for k in COMPONENT_ALIASES if not k.isdigit()}),
                "matchCount": 0,
                "parts": [],
            }

    cutoff = _parse_date(created_after)
    if created_within_years:
        relative = date.today() - timedelta(days=int(365.25 * float(created_within_years)))
        cutoff = max(cutoff, relative) if cutoff else relative

    matches: list[dict[str, Any]] = []
    for part in parts:
        if wanted_code and _component_code_of(part["partNumber"]) != wanted_code:
            continue
        if max_weight_kg is not None and part["weightKg"] > float(max_weight_kg):
            continue
        if vehicle_model and part["vehicleModel"].upper() != vehicle_model.strip().upper():
            continue
        if cutoff:
            created = _parse_date(part["createdDate"])
            if created is None or created < cutoff:
                continue
        matches.append(
            {
                "partNumber": part["partNumber"],
                "name": part["name"],
                "component": part["component"],
                "vehicleModel": part["vehicleModel"],
                "materialGrade": part["materialGrade"],
                "weightKg": part["weightKg"],
                "createdDate": part["createdDate"],
                "status": part["status"],
                "supplier": part["supplier"],
                "drawingRef": part["drawingRef"],
            }
        )

    matches.sort(key=lambda p: p["weightKg"])
    return {
        "source": "KVS (mock, via MCP)",
        "filtersApplied": {
            "component": component,
            "componentCode": wanted_code,
            "max_weight_kg": max_weight_kg,
            "created_within_years": created_within_years,
            "created_after": cutoff.isoformat() if cutoff else None,
            "vehicle_model": vehicle_model,
        },
        "catalogSize": len(parts),
        "matchCount": len(matches),
        "parts": matches,
        "note": (
            "Coarse attribute search only -- no drawing or feature data considered. "
            "Run the fine search over these part numbers to establish features such as a soft foot."
        ),
    }


@mcp.tool()
def get_part_record(part_number: str) -> dict[str, Any]:
    """Fetch the full KVS master record for one part number (exact match).

    Args:
        part_number: KVS part number, e.g. "10A.507.109".
    """
    target = part_number.strip().upper()
    for part in _load_catalog():
        if part["partNumber"].upper() == target:
            return part
    return {"error": f"Part {target} not found in KVS."}


@mcp.tool()
def fetch_drawing_ocr(part_number: str) -> dict[str, Any]:
    """Retrieve one part's drawing document from KVS and return its OCR text.

    This is the raw text only -- it does not decide anything about features.
    Use the drawing-analysis tools to evaluate it. For screening a batch of
    candidates, use the fine-search tool instead of calling this per part.

    Args:
        part_number: KVS part number, e.g. "10A.507.109".
    """
    target = part_number.strip().upper()
    record = next((p for p in _load_catalog() if p["partNumber"].upper() == target), None)
    if record is None:
        return {"error": f"Part {target} not found in KVS; no drawing available."}
    return get_drawing_ocr(target, record["drawingRef"])


if __name__ == "__main__":
    mcp.run(transport="stdio")
