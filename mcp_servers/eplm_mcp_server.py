"""Minimal stdio MCP server exposing the mock ePLM coarse search.

This is the demo's real MCP integration point: the Part Search Agent does not
call this code in-process, it speaks MCP to this script over stdio.

Scope is deliberately narrow, per the design brief: coarse search filters on
*attributes only* -- material, weight, publication date. No geometry, drawing
or feature logic is allowed at this stage; that is the fine-search step's job
(tools/drawing_analysis.py).

PRODUCTION SWAP-IN: replace `_load_catalog()` with a call to the real ePLM
query API (REST/OData) and keep this tool signature. The agent side does not
change at all.

Run standalone for a sanity check:  python mcp_servers/eplm_mcp_server.py
(it will sit waiting for MCP traffic on stdin -- Ctrl-C to exit)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

CATALOG_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "eplm_catalog.json"

mcp = FastMCP(
    "eplm",
    instructions="Mock ePLM part catalog: coarse attribute search over material, weight and publication date.",
)


def _load_catalog() -> list[dict[str, Any]]:
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["parts"]


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@mcp.tool()
def search_eplm_coarse(
    material: str | None = None,
    max_weight_kg: float | None = None,
    published_after: str | None = None,
) -> dict[str, Any]:
    """Coarse attribute search over the ePLM part catalog.

    Filters on material, maximum weight and publication date ONLY. This stage
    has no access to drawings or geometry -- use the drawing-analysis tool
    afterwards to compare mounting patterns and features.

    Args:
        material: Material family to match, e.g. "aluminium", "steel",
            "magnesium", "cfrp". Case-insensitive. Omit to match any material.
        max_weight_kg: Return only parts at or below this mass in kilograms.
        published_after: ISO date (YYYY-MM-DD); return only parts published on
            or after this date.

    Returns:
        A dict with the applied filters, a match count, and the matching parts
        (partNumber plus basic metadata).
    """
    parts = _load_catalog()
    cutoff = _parse_date(published_after) if published_after else None

    matches: list[dict[str, Any]] = []
    for part in parts:
        if material and part["material"].lower() != material.strip().lower():
            continue
        if max_weight_kg is not None and part["weightKg"] > float(max_weight_kg):
            continue
        if cutoff:
            published = _parse_date(part["publishedDate"])
            if published is None or published < cutoff:
                continue
        matches.append(
            {
                "partNumber": part["partNumber"],
                "name": part["name"],
                "material": part["material"],
                "materialGrade": part["materialGrade"],
                "weightKg": part["weightKg"],
                "publishedDate": part["publishedDate"],
                "program": part["program"],
                "status": part["status"],
                "drawingRef": part["drawingRef"],
            }
        )

    matches.sort(key=lambda p: p["weightKg"])
    return {
        "source": "ePLM (mock, via MCP)",
        "filtersApplied": {
            "material": material,
            "max_weight_kg": max_weight_kg,
            "published_after": published_after,
        },
        "matchCount": len(matches),
        "parts": matches,
        "note": "Coarse attribute search only -- no geometry or drawing data considered.",
    }


@mcp.tool()
def get_part_record(part_number: str) -> dict[str, Any]:
    """Fetch the full ePLM record for one part number (exact match)."""
    for part in _load_catalog():
        if part["partNumber"].upper() == part_number.strip().upper():
            return part
    return {"error": f"Part {part_number} not found in ePLM catalog."}


if __name__ == "__main__":
    mcp.run(transport="stdio")
