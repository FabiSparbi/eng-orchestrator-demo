"""Fine search: soft-foot detection from drawing OCR text.

The coarse KVS search filters on catalog attributes and can say nothing about
features. This step processes each candidate's drawing and decides whether the
part has a SOFT FOOT.

THE RULE
--------
A soft foot means the part has a tailored hardness profile: the upper section
is fully hardened while the foot area is left soft for crash and joining
behaviour. On the drawing that shows up as TWO OR MORE DIFFERENT HV callouts.
A part with a single uniform hardness value does not have a soft foot.

This is applied deterministically, in code, by `evaluate_soft_foot`. In a
production system the OCR text would instead be handed to a general-purpose LLM
along with a description of this rule; that is an explicit later step, and the
rule lives in one function here so it can be swapped for that call.

COST
----
Fine search is the expensive stage: one drawing fetch plus one OCR pass per
part. `run_fine_search` simulates that latency (about a second per part, capped
so the whole batch stays well under ten seconds). Set FINE_SEARCH_DELAY_S=0 to
disable it -- the test suite does.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from mock_data.drawing_ocr import get_drawing_ocr

CATALOG_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "kvs_catalog.json"

SOFT_FOOT_RULE = (
    "A drawing showing two or more different HV hardness values indicates a "
    "tailored hardness profile, i.e. a soft foot. A single uniform HV value "
    "means no soft foot."
)

# Matches "480 +/- 30 HV10", "200 HV 10", "480HV" and similar.
_HV_PATTERN = re.compile(r"(\d{2,4})\s*(?:\+/-\s*\d+\s*)?HV\s*\d*", re.IGNORECASE)

# Per-part simulated cost of fetching a drawing from KVS and OCR-ing it.
_DEFAULT_DELAY_S = 1.0
# Hard ceiling on a whole fine-search batch, so a demo never stalls.
MAX_FINE_SEARCH_SECONDS = 9.0


def _catalog_index() -> dict[str, dict[str, Any]]:
    """Part master data, keyed by part number.

    The fine search reports each candidate with its catalog attributes next to
    the drawing verdict, so the engineer sees one complete table instead of
    having to join two tool outputs by hand. In production this metadata comes
    back from the KVS record fetched alongside the drawing.
    """
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return {p["partNumber"].upper(): p for p in json.load(fh)["parts"]}


def _delay_per_part() -> float:
    raw = os.environ.get("FINE_SEARCH_DELAY_S")
    if raw is None:
        return _DEFAULT_DELAY_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_DELAY_S


def extract_hv_values(ocr_text: list[str]) -> list[int]:
    """Return every distinct HV hardness value found in OCR text, in order."""
    values: list[int] = []
    for line in ocr_text:
        for match in _HV_PATTERN.finditer(line):
            value = int(match.group(1))
            if value not in values:
                values.append(value)
    return values


def evaluate_soft_foot(ocr_text: list[str]) -> dict[str, Any]:
    """Apply the soft-foot rule to a drawing's OCR text.

    Args:
        ocr_text: The OCR'd lines of the drawing.

    Returns:
        The verdict, the HV values found, and the exact lines they came from so
        the decision can be checked against the drawing.
    """
    values = extract_hv_values(ocr_text)
    evidence = [line for line in ocr_text if _HV_PATTERN.search(line)]
    has_soft_foot = len(values) >= 2

    if has_soft_foot:
        reason = (
            f"{len(values)} different hardness values found ({', '.join(f'{v} HV' for v in values)}) "
            "-> tailored hardness profile -> soft foot."
        )
    elif values:
        reason = f"Only one hardness value found ({values[0]} HV) -> uniform hardening -> no soft foot."
    else:
        reason = "No HV hardness callout found on the drawing -> soft foot cannot be established."

    return {
        "hasSoftFoot": has_soft_foot,
        "hvValues": values,
        "evidence": evidence,
        "reason": reason,
        "rule": SOFT_FOOT_RULE,
    }


def analyze_drawing(part_number: str, drawing_ref: str | None = None) -> dict[str, Any]:
    """Fetch, OCR and evaluate ONE part's drawing.

    Use this when the engineer asks about a single specific part. For screening
    a batch of candidates from the coarse search, use `run_fine_search`.

    Args:
        part_number: KVS part number, e.g. "10A.507.109".
        drawing_ref: Optional drawing reference from the KVS record.

    Returns:
        The soft-foot verdict with the HV evidence, plus OCR metadata.
    """
    ocr = get_drawing_ocr(part_number, drawing_ref)
    verdict = evaluate_soft_foot(ocr["ocrText"])
    return {
        "partNumber": ocr["partNumber"],
        "drawingRef": ocr["drawingRef"],
        "scanQualityPct": ocr["scanQualityPct"],
        "hasSoftFoot": verdict["hasSoftFoot"],
        "hvValues": verdict["hvValues"],
        "evidence": verdict["evidence"],
        "reason": verdict["reason"],
        "ocrText": ocr["ocrText"],
        "method": "drawing retrieved from KVS (mock) + OCR (mock) + rule-based evaluation",
    }


async def run_fine_search(part_numbers: list[str]) -> dict[str, Any]:
    """Screen a batch of candidate parts for the soft-foot feature.

    This is the expensive stage: every part's drawing is retrieved from KVS and
    OCR'd before the rule is applied. Pass the part numbers returned by the
    coarse KVS search.

    Args:
        part_numbers: KVS part numbers to screen, e.g. ["10A.507.109", ...].

    Returns:
        Every screened part with its soft-foot verdict and HV evidence, plus a
        summary naming which parts qualify. Parts are NOT filtered out -- the
        engineer sees the full list with each verdict and picks one.
    """
    parts = [pn.strip().upper() for pn in part_numbers if pn and pn.strip()]
    if not parts:
        return {"error": "No part numbers given to screen.", "screened": []}

    # Spread the simulated cost across the batch, under the hard ceiling.
    per_part = _delay_per_part()
    if per_part * len(parts) > MAX_FINE_SEARCH_SECONDS:
        per_part = MAX_FINE_SEARCH_SECONDS / len(parts)

    catalog = _catalog_index()
    screened: list[dict[str, Any]] = []
    for part_number in parts:
        if per_part:
            await asyncio.sleep(per_part)
        result = analyze_drawing(part_number)
        record = catalog.get(result["partNumber"], {})
        screened.append(
            {
                "partNumber": result["partNumber"],
                "name": record.get("name"),
                "vehicleModel": record.get("vehicleModel"),
                "weightKg": record.get("weightKg"),
                "createdDate": record.get("createdDate"),
                "materialGrade": record.get("materialGrade"),
                "status": record.get("status"),
                "drawingRef": result["drawingRef"],
                "hasSoftFoot": result["hasSoftFoot"],
                "hvValues": result["hvValues"],
                "evidence": result["evidence"],
                "reason": result["reason"],
                "scanQualityPct": result["scanQualityPct"],
            }
        )

    screened.sort(key=lambda p: (not p["hasSoftFoot"], p["weightKg"] if p["weightKg"] is not None else 999))
    qualifying = [p["partNumber"] for p in screened if p["hasSoftFoot"]]
    return {
        "screenedCount": len(screened),
        "approxSecondsSpent": round(per_part * len(parts), 1),
        "rule": SOFT_FOOT_RULE,
        "screened": screened,
        "partsWithSoftFoot": qualifying,
        "summary": (
            f"Screened {len(screened)} drawing(s); {len(qualifying)} have a soft foot"
            + (f": {', '.join(qualifying)}." if qualifying else ".")
        ),
        "note": "Every screened part is listed with its verdict so the engineer can review and choose.",
    }
