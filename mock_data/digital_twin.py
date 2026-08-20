"""Mock 'digital twin': per-part simulation state shared by the Simulation and
Geometry agents.

=============================================================================
PERSISTENCE NOTE (first thing to swap out for a real deployment)
-----------------------------------------------------------------------------
This state lives in a plain module-level Python dict, so it exists only for the
life of the process and resets on every restart. That is deliberate for a
local, single-process DevUI demo. If state must survive restarts or be shared
across multiple agent processes/replicas, replace `_TWIN` with a small store
behind the same accessor functions (get_part_state / apply_severity_reduction /
reset_part): a JSON file for a single machine, SQLite for a single host with
concurrency, or Redis once the agents are hosted in Foundry and scaled out.
Nothing outside this module touches `_TWIN` directly, so the swap is local.
=============================================================================

Physically this stands in for a CAE results database: the "current" simulation
verdict for a part, which improves as geometry modifications are applied.
"""

from __future__ import annotations

import copy
import random
from typing import Any

# A region stops counting as "critical" once its severity drops below this.
SEVERITY_THRESHOLD = 0.30

# Deterministic jitter: metrics wobble a little (so the demo does not look like
# a lookup table) but severities -- which drive convergence -- never do.
_rng = random.Random(20240612)

ANALYSIS_TYPES = ("stiffness", "modal", "stampability")

# Baseline "as designed" state per part. Severity in [0, 1]; higher is worse.
_BASELINE: dict[str, dict[str, Any]] = {
    "BR-3310": {
        "stiffness": {
            "metrics": {"maxDisplacementMm": 1.42, "targetDisplacementMm": 1.00, "stiffnessNPerMm": 8450.0},
            "areas": [
                {
                    "regionId": "R-STF-01",
                    "location": "Upper mounting flange, outboard edge",
                    "severity": 0.62,
                    "description": "Displacement 42% over target under 3.5 kN lateral load.",
                },
                {
                    "regionId": "R-STF-02",
                    "location": "Web transition near bolt hole 3",
                    "severity": 0.38,
                    "description": "Local stress concentration, factor 1.8 vs. nominal.",
                },
            ],
        },
        "modal": {
            "metrics": {"firstModeHz": 186.0, "targetMinHz": 210.0, "dampingRatio": 0.021},
            "areas": [
                {
                    "regionId": "R-MOD-01",
                    "location": "Free end of lower arm",
                    "severity": 0.55,
                    "description": "First bending mode at 186 Hz, below the 210 Hz idle-excitation limit.",
                }
            ],
        },
        "stampability": {
            "metrics": {"maxThinningPct": 27.4, "thinningLimitPct": 20.0, "drawDepthMm": 38.0},
            "areas": [
                {
                    "regionId": "R-STP-01",
                    "location": "Deep draw corner, front left radius",
                    "severity": 0.71,
                    "description": "Thinning 27.4% exceeds the 20% limit; splitting risk at radius R6.",
                },
                {
                    "regionId": "R-STP-02",
                    "location": "Flange wrap, rear edge",
                    "severity": 0.44,
                    "description": "Wrinkling predicted at blank-holder force below 480 kN.",
                },
            ],
        },
    },
    "BR-5501": {
        "stiffness": {
            "metrics": {"maxDisplacementMm": 1.08, "targetDisplacementMm": 1.00, "stiffnessNPerMm": 10250.0},
            "areas": [
                {
                    "regionId": "R-STF-01",
                    "location": "Casting rib junction, inboard",
                    "severity": 0.41,
                    "description": "Displacement 8% over target; rib section undersized.",
                }
            ],
        },
        "modal": {
            "metrics": {"firstModeHz": 224.0, "targetMinHz": 210.0, "dampingRatio": 0.028},
            "areas": [],
        },
        "stampability": {
            "metrics": {"maxThinningPct": 12.0, "thinningLimitPct": 20.0, "drawDepthMm": 22.0},
            "areas": [
                {
                    "regionId": "R-STP-01",
                    "location": "Porosity-prone boss, cast feature",
                    "severity": 0.35,
                    "description": "Cast section thickness variation risks shrinkage porosity.",
                }
            ],
        },
    },
    "BR-7150": {
        "stiffness": {
            "metrics": {"maxDisplacementMm": 1.95, "targetDisplacementMm": 1.20, "stiffnessNPerMm": 6100.0},
            "areas": [
                {
                    "regionId": "R-STF-01",
                    "location": "Tray interface, mid-span",
                    "severity": 0.68,
                    "description": "Mid-span sag 63% over target for the battery load case.",
                }
            ],
        },
        "modal": {
            "metrics": {"firstModeHz": 158.0, "targetMinHz": 180.0, "dampingRatio": 0.019},
            "areas": [
                {
                    "regionId": "R-MOD-01",
                    "location": "Tray interface, mid-span",
                    "severity": 0.47,
                    "description": "First mode at 158 Hz sits inside the road-input band.",
                }
            ],
        },
        "stampability": {
            "metrics": {"maxThinningPct": 21.8, "thinningLimitPct": 20.0, "drawDepthMm": 31.0},
            "areas": [
                {
                    "regionId": "R-STP-01",
                    "location": "Side wall draw radius",
                    "severity": 0.33,
                    "description": "Thinning marginally over limit at 21.8%.",
                }
            ],
        },
    },
}

# Any part not in _BASELINE gets this generic profile, so the demo never
# dead-ends on an unexpected part number.
_GENERIC = {
    "stiffness": {
        "metrics": {"maxDisplacementMm": 1.30, "targetDisplacementMm": 1.00, "stiffnessNPerMm": 7800.0},
        "areas": [
            {
                "regionId": "R-STF-01",
                "location": "Primary load path, mid-section",
                "severity": 0.52,
                "description": "Displacement 30% over target under the nominal load case.",
            }
        ],
    },
    "modal": {
        "metrics": {"firstModeHz": 192.0, "targetMinHz": 210.0, "dampingRatio": 0.022},
        "areas": [
            {
                "regionId": "R-MOD-01",
                "location": "Unsupported free edge",
                "severity": 0.40,
                "description": "First bending mode below the target floor.",
            }
        ],
    },
    "stampability": {
        "metrics": {"maxThinningPct": 24.0, "thinningLimitPct": 20.0, "drawDepthMm": 34.0},
        "areas": [
            {
                "regionId": "R-STP-01",
                "location": "Deepest draw radius",
                "severity": 0.58,
                "description": "Thinning 24% exceeds the 20% limit.",
            }
        ],
    },
}

# The live state. part_id -> {"analyses": {...}, "modifications": [...]}
_TWIN: dict[str, dict[str, Any]] = {}


def _ensure_part(part_id: str) -> dict[str, Any]:
    """Lazily seed a part's state from its baseline on first access."""
    if part_id not in _TWIN:
        baseline = _BASELINE.get(part_id, _GENERIC)
        _TWIN[part_id] = {
            "analyses": copy.deepcopy(baseline),
            "modifications": [],
        }
    return _TWIN[part_id]


def get_part_state(part_id: str) -> dict[str, Any]:
    """Return the live state for a part (seeding it from baseline if new)."""
    return _ensure_part(part_id)


def jitter(value: float, pct: float = 0.02) -> float:
    """Small deterministic wobble on a reported metric, never on a severity."""
    return round(value * (1.0 + _rng.uniform(-pct, pct)), 3)


def critical_areas(part_id: str, analysis_type: str) -> list[dict[str, Any]]:
    """Regions still above the severity threshold for one analysis type."""
    state = _ensure_part(part_id)
    areas = state["analyses"][analysis_type]["areas"]
    return [
        {
            "regionId": a["regionId"],
            "location": a["location"],
            "severity": round(a["severity"], 3),
            "description": a["description"],
        }
        for a in areas
        if a["severity"] >= SEVERITY_THRESHOLD
    ]


def max_severity(part_id: str, analysis_type: str) -> float:
    """Worst remaining severity for one analysis type (0.0 if all resolved)."""
    state = _ensure_part(part_id)
    areas = state["analyses"][analysis_type]["areas"]
    return round(max((a["severity"] for a in areas), default=0.0), 3)


def apply_severity_reduction(
    part_id: str,
    reduction: float,
    target_analysis: str | None = None,
    target_region: str | None = None,
) -> dict[str, Any]:
    """Improve the twin after an approved geometry modification.

    This is what makes the CAD/CAE loop converge: each applied modification
    knocks `reduction` off the severity of the affected regions, so a later
    simulation call sees fewer critical areas and eventually reports "pass".

    A targeted change (one analysis type / one region) bites harder on its
    target and still helps a little elsewhere -- a stand-in for the fact that
    stiffening a rib also shifts the modal response.
    """
    state = _ensure_part(part_id)
    touched: list[str] = []

    for analysis_type in ANALYSIS_TYPES:
        for area in state["analyses"][analysis_type]["areas"]:
            if target_region and area["regionId"] != target_region:
                continue
            if target_analysis and analysis_type != target_analysis:
                # Off-target coupling effect: a fraction of the benefit.
                delta = reduction * 0.35
            else:
                delta = reduction
            if delta <= 0:
                continue
            before = area["severity"]
            area["severity"] = max(0.0, round(before - delta, 3))
            if area["severity"] < before:
                touched.append(f"{analysis_type}/{area['regionId']}")

        # Move headline metrics toward target in step with the severity drop.
        _improve_metrics(state["analyses"][analysis_type], analysis_type, reduction)

    return {
        "partId": part_id,
        "regionsImproved": touched,
        "remainingSeverity": {a: max_severity(part_id, a) for a in ANALYSIS_TYPES},
    }


def _improve_metrics(block: dict[str, Any], analysis_type: str, reduction: float) -> None:
    """Nudge the reported metrics in the physically sensible direction."""
    m = block["metrics"]
    scale = max(0.0, min(1.0, reduction))
    if analysis_type == "stiffness":
        m["maxDisplacementMm"] = round(max(0.1, m["maxDisplacementMm"] * (1 - 0.30 * scale)), 3)
        m["stiffnessNPerMm"] = round(m["stiffnessNPerMm"] * (1 + 0.25 * scale), 1)
    elif analysis_type == "modal":
        m["firstModeHz"] = round(m["firstModeHz"] * (1 + 0.12 * scale), 1)
    elif analysis_type == "stampability":
        m["maxThinningPct"] = round(max(2.0, m["maxThinningPct"] * (1 - 0.28 * scale)), 2)


def record_modification(part_id: str, entry: dict[str, Any]) -> int:
    """Append an applied modification to the part's history; return its index."""
    state = _ensure_part(part_id)
    state["modifications"].append(entry)
    return len(state["modifications"])


def modification_history(part_id: str) -> list[dict[str, Any]]:
    return list(_ensure_part(part_id)["modifications"])


def reset_part(part_id: str) -> None:
    """Drop a part back to its baseline (used by tests and demo re-runs)."""
    _TWIN.pop(part_id, None)


def reset_all() -> None:
    _TWIN.clear()
