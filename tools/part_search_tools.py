"""Result delivery for the Part Search Agent.

Combines the two search stages -- coarse ePLM attribute search (over MCP) and
fine drawing analysis -- into the ranked-candidate schema the design brief
specifies. Keeping this as a deterministic tool (rather than asking the model
to assemble JSON by hand) means the schema and the ranking are reproducible.

NOTE ON SCHEMA: the field names below follow the brief's ranked-candidate
structure. If the canonical field names differ from these, change them here --
this is the single place the output schema is defined.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.drawing_analysis import analyze_drawing, compare_mounting_interfaces

CATALOG_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "eplm_catalog.json"


def _catalog() -> dict[str, dict[str, Any]]:
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return {p["partNumber"].upper(): p for p in json.load(fh)["parts"]}


def rank_candidates(reference_part: str, candidate_parts: list[str]) -> dict[str, Any]:
    """Rank candidate replacement parts against a reference part.

    Scores each candidate on how closely its mounting interface matches the
    reference (from drawing analysis) and how much mass it saves, then returns
    them best-first in the standard candidate schema.

    Args:
        reference_part: The part being replaced, e.g. "BR-2201".
        candidate_parts: Part numbers from the coarse ePLM search, e.g.
            ["BR-3310", "BR-5501"].

    Returns:
        A dict with the reference part and a ranked `candidates` list.
    """
    catalog = _catalog()
    ref_pn = reference_part.strip().upper()
    ref_record = catalog.get(ref_pn)
    ref_weight = ref_record["weightKg"] if ref_record else None

    candidates: list[dict[str, Any]] = []
    for raw in candidate_parts:
        pn = raw.strip().upper()
        record = catalog.get(pn)
        if record is None:
            continue

        comparison = compare_mounting_interfaces(ref_pn, pn)
        drawing = analyze_drawing(pn)

        weight_saving = round(ref_weight - record["weightKg"], 3) if ref_weight is not None else None
        saving_pct = (
            round(100.0 * weight_saving / ref_weight, 1)
            if ref_weight not in (None, 0) and weight_saving is not None
            else None
        )

        # Ranking: geometric fit dominates, weight saving is the tie-breaker,
        # and a prototype/obsolete lifecycle status is penalised.
        fit = comparison["geometricMatchScore"]
        saving_term = max(0.0, min(1.0, (saving_pct or 0.0) / 50.0))
        status_penalty = {"released": 0.0, "prototype": 0.15, "obsolete": 0.35}.get(record["status"], 0.1)
        match_score = round(max(0.0, min(1.0, 0.65 * fit + 0.35 * saving_term - status_penalty)), 3)

        candidates.append(
            {
                "partNumber": record["partNumber"],
                "name": record["name"],
                "matchScore": match_score,
                "material": record["material"],
                "materialGrade": record["materialGrade"],
                "weightKg": record["weightKg"],
                "weightSavingKg": weight_saving,
                "weightSavingPct": saving_pct,
                "mountingPatternMatch": comparison["mountingPatternMatch"],
                "geometricMatchScore": fit,
                "holeCount": drawing["hole_count"],
                "mountingPattern": drawing["mounting_pattern"],
                "distinguishingFeatures": drawing["distinguishing_features"],
                "differences": comparison["differences"],
                "lifecycleStatus": record["status"],
                "confidence": comparison["confidence"],
                "rationale": (
                    f"{comparison['mountingPatternMatch'].capitalize()} mounting-interface match "
                    f"(score {fit}); "
                    + (
                        f"saves {weight_saving} kg ({saving_pct}%) vs. {ref_pn}."
                        if weight_saving is not None
                        else "weight comparison unavailable."
                    )
                ),
            }
        )

    candidates.sort(key=lambda c: c["matchScore"], reverse=True)
    for i, candidate in enumerate(candidates, start=1):
        candidate["rank"] = i

    return {
        "referencePart": ref_pn,
        "referenceWeightKg": ref_weight,
        "candidateCount": len(candidates),
        "candidates": candidates,
        "method": "coarse ePLM attribute search (MCP) + fine drawing analysis (mock)",
    }
