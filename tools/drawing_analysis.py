"""Fine search: mocked engineering-drawing analysis.

Stands in for a vision model reading a 2D drawing (or a feature-recognition
pass over CAD) to extract mounting geometry that ePLM attribute search cannot
see. Coarse search narrows the field on material/weight/date; this step decides
whether a candidate actually bolts into the same place.

PRODUCTION SWAP-IN: replace the fixtures below with a call to a vision-capable
model over the drawing PDF/TIFF referenced by `drawingRef`, or with a CAD
feature-recognition service. Keep the return shape and everything downstream
(ranking, agent instructions) still works.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "eplm_catalog.json"

# Per-part "what the drawing shows". Hand-authored so the demo story holds:
# BR-3310 and BR-5501 are genuine drop-in candidates for BR-2201; BR-4120 is
# lighter but has a different bolt pattern, which is exactly the kind of thing
# coarse attribute search cannot catch.
_DRAWING_FIXTURES: dict[str, dict[str, Any]] = {
    "BR-2201": {
        "hole_count": 4,
        "mounting_pattern": "4x M10 on 120x80 mm rectangular pitch",
        "distinguishing_features": ["stiffening rib along load path", "45 deg chamfered outboard edge", "drain notch"],
        "confidence": 0.94,
    },
    "BR-3310": {
        "hole_count": 4,
        "mounting_pattern": "4x M10 on 120x80 mm rectangular pitch",
        "distinguishing_features": ["twin stiffening ribs", "weight-relief pocket", "45 deg chamfered outboard edge"],
        "confidence": 0.91,
    },
    "BR-3412": {
        "hole_count": 4,
        "mounting_pattern": "4x M10 on 120x82 mm rectangular pitch",
        "distinguishing_features": ["single deep rib", "no drain notch"],
        "confidence": 0.88,
    },
    "BR-4120": {
        "hole_count": 3,
        "mounting_pattern": "3x M12 on 100 mm triangular pitch",
        "distinguishing_features": ["cast lattice web", "integrated sensor boss"],
        "confidence": 0.79,
    },
    "BR-2890": {
        "hole_count": 5,
        "mounting_pattern": "5x M10 irregular pitch, damper-specific",
        "distinguishing_features": ["damper eye interface", "welded gusset"],
        "confidence": 0.86,
    },
    "BR-5501": {
        "hole_count": 4,
        "mounting_pattern": "4x M10 on 120x80 mm rectangular pitch",
        "distinguishing_features": ["cast rib network", "machined mating face", "drain notch"],
        "confidence": 0.89,
    },
    "BR-6002": {
        "hole_count": 6,
        "mounting_pattern": "6x M8 on 140x60 mm rectangular pitch",
        "distinguishing_features": ["extruded profile section", "end-milled interfaces"],
        "confidence": 0.83,
    },
    "BR-7150": {
        "hole_count": 4,
        "mounting_pattern": "4x M8 on 150x90 mm rectangular pitch",
        "distinguishing_features": ["tray location pins", "thermal isolation pad seat"],
        "confidence": 0.87,
    },
    "BR-8021": {
        "hole_count": 4,
        "mounting_pattern": "4x M10 on 120x80 mm rectangular pitch (bonded inserts)",
        "distinguishing_features": ["bonded metal inserts", "laminate ply drop-offs"],
        "confidence": 0.68,
    },
    "BR-1180": {
        "hole_count": 4,
        "mounting_pattern": "4x M12 on 160x100 mm rectangular pitch",
        "distinguishing_features": ["heavy cast boss", "legacy datum scheme"],
        "confidence": 0.72,
    },
}

_rng = random.Random(4711)


def _catalog_record(part_number: str) -> dict[str, Any] | None:
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        for part in json.load(fh)["parts"]:
            if part["partNumber"].upper() == part_number.strip().upper():
                return part
    return None


def analyze_drawing(part_number: str) -> dict[str, Any]:
    """Extract mounting geometry from a part's engineering drawing.

    Fine-search step: reads the drawing referenced by the ePLM record and
    reports the mounting interface, so candidates can be compared on whether
    they physically fit -- something the coarse attribute search cannot do.

    Args:
        part_number: ePLM part number, e.g. "BR-2201".

    Returns:
        Hole count, mounting pattern description, distinguishing features and
        an extraction confidence in [0, 1].
    """
    pn = part_number.strip().upper()
    record = _catalog_record(pn)
    fixture = _DRAWING_FIXTURES.get(pn)

    if fixture is None:
        # Unknown part: report low confidence rather than inventing geometry.
        return {
            "partNumber": pn,
            "drawingRef": record["drawingRef"] if record else None,
            "hole_count": None,
            "mounting_pattern": "unknown -- no drawing available",
            "distinguishing_features": [],
            "confidence": 0.0,
            "note": "No drawing on file for this part number (mock analysis).",
        }

    return {
        "partNumber": pn,
        "drawingRef": record["drawingRef"] if record else None,
        "hole_count": fixture["hole_count"],
        "mounting_pattern": fixture["mounting_pattern"],
        "distinguishing_features": list(fixture["distinguishing_features"]),
        "confidence": round(min(1.0, fixture["confidence"] + _rng.uniform(-0.02, 0.02)), 3),
        "source": "drawing analysis (mock vision extraction)",
    }


def compare_mounting_interfaces(reference_part: str, candidate_part: str) -> dict[str, Any]:
    """Compare two parts' mounting interfaces from their drawings.

    Use after `analyze_drawing` to judge whether a candidate is a drop-in
    replacement for the reference part.

    Args:
        reference_part: The part currently in the design, e.g. "BR-2201".
        candidate_part: The proposed alternative, e.g. "BR-3310".

    Returns:
        A match verdict ("exact", "similar", "different"), a 0-1 geometric
        match score, and the specific differences found.
    """
    ref = analyze_drawing(reference_part)
    cand = analyze_drawing(candidate_part)

    if ref["hole_count"] is None or cand["hole_count"] is None:
        return {
            "referencePart": ref["partNumber"],
            "candidatePart": cand["partNumber"],
            "mountingPatternMatch": "unknown",
            "geometricMatchScore": 0.0,
            "differences": ["Drawing data missing for at least one part."],
            "confidence": 0.0,
        }

    differences: list[str] = []
    score = 1.0

    if ref["hole_count"] != cand["hole_count"]:
        differences.append(
            f"Hole count differs: reference has {ref['hole_count']}, candidate has {cand['hole_count']}."
        )
        score -= 0.45

    ref_pattern = ref["mounting_pattern"]
    cand_pattern = cand["mounting_pattern"]
    if ref_pattern != cand_pattern:
        # Same thread size and roughly the same pitch is a "similar" fit.
        ref_thread = ref_pattern.split()[1] if len(ref_pattern.split()) > 1 else ""
        cand_thread = cand_pattern.split()[1] if len(cand_pattern.split()) > 1 else ""
        if ref_thread == cand_thread:
            differences.append(f"Pitch differs slightly: '{ref_pattern}' vs '{cand_pattern}'.")
            score -= 0.15
        else:
            differences.append(f"Fastener size differs: '{ref_pattern}' vs '{cand_pattern}'.")
            score -= 0.35

    shared = set(ref["distinguishing_features"]) & set(cand["distinguishing_features"])
    missing = set(ref["distinguishing_features"]) - set(cand["distinguishing_features"])
    if missing:
        differences.append("Features absent on candidate: " + ", ".join(sorted(missing)) + ".")
        score -= 0.05 * len(missing)

    score = round(max(0.0, min(1.0, score)), 3)
    if score >= 0.9:
        verdict = "exact"
    elif score >= 0.6:
        verdict = "similar"
    else:
        verdict = "different"

    return {
        "referencePart": ref["partNumber"],
        "candidatePart": cand["partNumber"],
        "mountingPatternMatch": verdict,
        "geometricMatchScore": score,
        "sharedFeatures": sorted(shared),
        "differences": differences or ["No geometric differences detected."],
        "confidence": round(min(ref["confidence"], cand["confidence"]), 3),
    }
