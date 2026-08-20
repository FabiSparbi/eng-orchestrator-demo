"""Mock 'digital twin': per-part simulation state shared by the Simulation and
Geometry agents.

=============================================================================
PERSISTENCE NOTE (first thing to swap out for a real deployment)
-----------------------------------------------------------------------------
This state lives in a plain module-level Python dict, so it exists only for the
life of the process and resets on every restart. That is deliberate for a
local, single-process DevUI demo. If state must survive restarts or be shared
across multiple agent processes/replicas, replace `_TWIN` with a small store
behind the same accessor functions: a JSON file for a single machine, SQLite
for a single host with concurrency, or Redis once the agents are hosted in
Foundry and scaled out. Nothing outside this module touches `_TWIN` directly.
=============================================================================

AGENT-FIXABLE vs. ENGINEER-ONLY REGIONS
---------------------------------------
A stamping simulation flags many critical regions. Only a small subset can be
resolved by the Geometry Agent: those attached to a named parametric feature
where the recommended fix is a parameter change (e.g. a hole whose radius must
grow). Everything else -- wrinkling flanges, thinning walls, transition radii --
needs a CAD engineer to open the part in CATIA and rework it.

Each region therefore carries `agentFixable`, and for the fixable ones the
feature id and the exact parametric operation. This split is the point of the
demo: the agent fixes what it legitimately can and hands the rest over.
"""

from __future__ import annotations

import copy
import random
from typing import Any

# A region stops counting as "critical" once its severity drops below this.
SEVERITY_THRESHOLD = 0.30

# Deterministic jitter: metrics wobble a little (so the demo does not look like
# a lookup table) but severities -- which drive convergence -- never do.
_rng = random.Random(20260820)

ANALYSIS_TYPES = ("stamping", "stiffness", "modal")

# The demo part: a B-pillar with a soft foot. Its stamping simulation flags
# seven critical regions, exactly ONE of which the Geometry Agent can fix.
_HERO_PART = "10A.507.109"

_BASELINE: dict[str, dict[str, Any]] = {
    _HERO_PART: {
        "stamping": {
            "metrics": {
                "maxThinningPct": 26.8,
                "thinningLimitPct": 20.0,
                "maxWrinklingIndex": 1.42,
                "wrinklingLimit": 1.00,
                "drawDepthMm": 61.0,
            },
            "areas": [
                {
                    "regionId": "R-STM-01",
                    "regionType": "hole",
                    "location": "Fastening hole D13.5, lower foot area",
                    "severity": 0.68,
                    "description": (
                        "Circumferential thinning of 26.8% around the hole edge; "
                        "splitting risk during draw. Hole is too small for the local strain."
                    ),
                    "suggestedFix": "Increase hole radius from 6.75 mm to 8.50 mm",
                    "agentFixable": True,
                    "featureId": "HOLE_D13_5_LH",
                    "parametricOp": "increase_hole_radius",
                    "currentValueMm": 6.75,
                    "suggestedValueMm": 8.50,
                },
                {
                    "regionId": "R-STM-02",
                    "regionType": "flange",
                    "location": "Upper weld flange, outboard edge",
                    "severity": 0.61,
                    "description": "Wrinkling index 1.42 exceeds limit; blank holder force insufficient over the flange run.",
                    "suggestedFix": "Rework flange geometry / revise blank holder layout in CATIA",
                    "agentFixable": False,
                },
                {
                    "regionId": "R-STM-03",
                    "regionType": "wall",
                    "location": "Draw wall, mid-section inboard",
                    "severity": 0.57,
                    "description": "Thinning 24.1% over a 90 mm run; material flow restricted by the draw depth.",
                    "suggestedFix": "Reduce local draw depth or add addendum relief in CATIA",
                    "agentFixable": False,
                },
                {
                    "regionId": "R-STM-04",
                    "regionType": "radius",
                    "location": "Transition radius, zone B (hardness transition)",
                    "severity": 0.54,
                    "description": "Splitting predicted at R6 where the tailored hardness transition crosses the draw radius.",
                    "suggestedFix": "Open transition radius and re-position the tempering zone in CATIA",
                    "agentFixable": False,
                },
                {
                    "regionId": "R-STM-05",
                    "regionType": "bead",
                    "location": "Draw bead, rear die face",
                    "severity": 0.47,
                    "description": "Bead engagement too low; insufficient restraining force during the draw.",
                    "suggestedFix": "Re-profile the draw bead in the die model (CATIA)",
                    "agentFixable": False,
                },
                {
                    "regionId": "R-STM-06",
                    "regionType": "flange",
                    "location": "Lower foot flange, trim edge",
                    "severity": 0.41,
                    "description": "Edge cracking predicted at the trim line in the soft foot zone.",
                    "suggestedFix": "Revise trim line and edge condition in CATIA",
                    "agentFixable": False,
                },
                {
                    "regionId": "R-STM-07",
                    "regionType": "wall",
                    "location": "Side wall near soft-zone boundary",
                    "severity": 0.36,
                    "description": "Strain concentration where hardened and soft zones meet.",
                    "suggestedFix": "Adjust wall draft and soft-zone boundary in CATIA",
                    "agentFixable": False,
                },
            ],
        },
        "stiffness": {
            "metrics": {"maxDisplacementMm": 1.31, "targetDisplacementMm": 1.10, "stiffnessNPerMm": 9120.0},
            "areas": [
                {
                    "regionId": "R-STF-01",
                    "regionType": "wall",
                    "location": "Upper section, lateral load path",
                    "severity": 0.44,
                    "description": "Displacement 19% over target under the side-impact load case.",
                    "suggestedFix": "Increase section depth or add a reinforcement rib in CATIA",
                    "agentFixable": False,
                }
            ],
        },
        "modal": {
            "metrics": {"firstModeHz": 214.0, "targetMinHz": 200.0, "dampingRatio": 0.024},
            "areas": [],
        },
    }
}

# Any part not in _BASELINE gets this generic profile, so the demo never
# dead-ends on an unexpected part number.
_GENERIC = {
    "stamping": {
        "metrics": {
            "maxThinningPct": 23.5,
            "thinningLimitPct": 20.0,
            "maxWrinklingIndex": 1.18,
            "wrinklingLimit": 1.00,
            "drawDepthMm": 52.0,
        },
        "areas": [
            {
                "regionId": "R-STM-01",
                "regionType": "hole",
                "location": "Fastening hole, lower section",
                "severity": 0.62,
                "description": "Thinning around the hole edge exceeds the forming limit.",
                "suggestedFix": "Increase hole radius from 6.00 mm to 7.50 mm",
                "agentFixable": True,
                "featureId": "HOLE_D12_0_LH",
                "parametricOp": "increase_hole_radius",
                "currentValueMm": 6.00,
                "suggestedValueMm": 7.50,
            },
            {
                "regionId": "R-STM-02",
                "regionType": "flange",
                "location": "Weld flange, outboard edge",
                "severity": 0.55,
                "description": "Wrinkling predicted along the flange run.",
                "suggestedFix": "Rework flange geometry in CATIA",
                "agentFixable": False,
            },
            {
                "regionId": "R-STM-03",
                "regionType": "wall",
                "location": "Draw wall, mid-section",
                "severity": 0.48,
                "description": "Thinning over the forming limit across the draw wall.",
                "suggestedFix": "Reduce local draw depth in CATIA",
                "agentFixable": False,
            },
        ],
    },
    "stiffness": {
        "metrics": {"maxDisplacementMm": 1.25, "targetDisplacementMm": 1.10, "stiffnessNPerMm": 8600.0},
        "areas": [
            {
                "regionId": "R-STF-01",
                "regionType": "wall",
                "location": "Primary load path, mid-section",
                "severity": 0.40,
                "description": "Displacement over target under the nominal load case.",
                "suggestedFix": "Add reinforcement in CATIA",
                "agentFixable": False,
            }
        ],
    },
    "modal": {
        "metrics": {"firstModeHz": 205.0, "targetMinHz": 200.0, "dampingRatio": 0.023},
        "areas": [],
    },
}

# The live state. part_id -> {"analyses": ..., "modifications": [...], "manualRework": [...]}
_TWIN: dict[str, dict[str, Any]] = {}


def _ensure_part(part_id: str) -> dict[str, Any]:
    """Lazily seed a part's state from its baseline on first access."""
    if part_id not in _TWIN:
        baseline = _BASELINE.get(part_id, _GENERIC)
        _TWIN[part_id] = {
            "analyses": copy.deepcopy(baseline),
            "modifications": [],
            "manualRework": [],
        }
    return _TWIN[part_id]


def get_part_state(part_id: str) -> dict[str, Any]:
    """Return the live state for a part (seeding it from baseline if new)."""
    return _ensure_part(part_id)


def jitter(value: float, pct: float = 0.02) -> float:
    """Small deterministic wobble on a reported metric, never on a severity."""
    return round(value * (1.0 + _rng.uniform(-pct, pct)), 3)


def _public_area(area: dict[str, Any]) -> dict[str, Any]:
    """The region fields agents and the UI see."""
    public = {
        "regionId": area["regionId"],
        "regionType": area["regionType"],
        "location": area["location"],
        "severity": round(area["severity"], 3),
        "description": area["description"],
        "suggestedFix": area["suggestedFix"],
        "agentFixable": area["agentFixable"],
    }
    if area["agentFixable"]:
        public.update(
            {
                "featureId": area.get("featureId"),
                "parametricOp": area.get("parametricOp"),
                "currentValueMm": area.get("currentValueMm"),
                "suggestedValueMm": area.get("suggestedValueMm"),
            }
        )
    return public


def critical_areas(part_id: str, analysis_type: str) -> list[dict[str, Any]]:
    """Regions still above the severity threshold, worst first."""
    state = _ensure_part(part_id)
    areas = [a for a in state["analyses"][analysis_type]["areas"] if a["severity"] >= SEVERITY_THRESHOLD]
    areas.sort(key=lambda a: a["severity"], reverse=True)
    return [_public_area(a) for a in areas]


def agent_fixable_areas(part_id: str, analysis_type: str) -> list[dict[str, Any]]:
    """Open critical regions the Geometry Agent can actually resolve."""
    return [a for a in critical_areas(part_id, analysis_type) if a["agentFixable"]]


def engineer_only_areas(part_id: str, analysis_type: str) -> list[dict[str, Any]]:
    """Open critical regions that need a CAD engineer, not the agent."""
    return [a for a in critical_areas(part_id, analysis_type) if not a["agentFixable"]]


def find_region(part_id: str, analysis_type: str, region_id: str) -> dict[str, Any] | None:
    """Locate one region's live record (internal representation)."""
    state = _ensure_part(part_id)
    target = region_id.strip().upper()
    for area in state["analyses"][analysis_type]["areas"]:
        if area["regionId"].upper() == target:
            return area
    return None


def max_severity(part_id: str, analysis_type: str) -> float:
    """Worst remaining severity for one analysis type (0.0 if all resolved)."""
    state = _ensure_part(part_id)
    return round(max((a["severity"] for a in state["analyses"][analysis_type]["areas"]), default=0.0), 3)


def resolve_region(part_id: str, analysis_type: str, region_id: str, reduction: float) -> dict[str, Any]:
    """Improve one region after an approved parametric change.

    Only the targeted region moves. A hole radius change does not fix a
    wrinkling flange, so there is deliberately no cross-region coupling here.
    """
    area = find_region(part_id, analysis_type, region_id)
    if area is None:
        return {"resolved": False, "reason": f"Region {region_id} not found for {analysis_type} on {part_id}."}

    before = area["severity"]
    area["severity"] = max(0.0, round(before - reduction, 3))
    _improve_metrics(_ensure_part(part_id)["analyses"][analysis_type], analysis_type, reduction)

    return {
        "resolved": area["severity"] < SEVERITY_THRESHOLD,
        "regionId": area["regionId"],
        "severityBefore": round(before, 3),
        "severityAfter": area["severity"],
    }


def _improve_metrics(block: dict[str, Any], analysis_type: str, reduction: float) -> None:
    """Nudge the reported metrics in the physically sensible direction."""
    m = block["metrics"]
    scale = max(0.0, min(1.0, reduction))
    if analysis_type == "stamping":
        m["maxThinningPct"] = round(max(2.0, m["maxThinningPct"] * (1 - 0.18 * scale)), 2)
    elif analysis_type == "stiffness":
        m["maxDisplacementMm"] = round(max(0.1, m["maxDisplacementMm"] * (1 - 0.25 * scale)), 3)
        m["stiffnessNPerMm"] = round(m["stiffnessNPerMm"] * (1 + 0.20 * scale), 1)
    elif analysis_type == "modal":
        m["firstModeHz"] = round(m["firstModeHz"] * (1 + 0.10 * scale), 1)


def record_manual_rework(
    part_id: str,
    analysis_type: str | None = None,
    region_ids: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Clear regions a CAD engineer reports having reworked by hand.

    This is the human half of the loop: the engineer opens the part in CATIA,
    makes the changes the agent could not, and says so. Their word is what
    updates the twin here -- nothing is verified, exactly as in a real handover.
    """
    state = _ensure_part(part_id)
    analyses = [analysis_type] if analysis_type else list(ANALYSIS_TYPES)
    wanted = {r.strip().upper() for r in region_ids} if region_ids else None

    cleared: list[str] = []
    for analysis in analyses:
        for area in state["analyses"][analysis]["areas"]:
            if area["severity"] < SEVERITY_THRESHOLD:
                continue
            if wanted is not None and area["regionId"].upper() not in wanted:
                continue
            if wanted is None and area["agentFixable"]:
                # Untargeted rework covers what the agent could not do; an
                # open agent-fixable region stays open so it is not silently
                # absorbed into a manual handover.
                continue
            area["severity"] = 0.0
            cleared.append(f"{analysis}/{area['regionId']}")

    entry = {"clearedRegions": cleared, "note": note, "analysisType": analysis_type}
    state["manualRework"].append(entry)
    return {"partId": part_id, "clearedRegions": cleared, "reworkCount": len(state["manualRework"])}


def record_modification(part_id: str, entry: dict[str, Any]) -> int:
    """Append an applied modification to the part's history; return its index."""
    state = _ensure_part(part_id)
    state["modifications"].append(entry)
    return len(state["modifications"])


def modification_history(part_id: str) -> list[dict[str, Any]]:
    return list(_ensure_part(part_id)["modifications"])


def manual_rework_history(part_id: str) -> list[dict[str, Any]]:
    return list(_ensure_part(part_id)["manualRework"])


def reset_part(part_id: str) -> None:
    """Drop a part back to its baseline (used by tests and demo re-runs)."""
    _TWIN.pop(part_id, None)


def reset_all() -> None:
    _TWIN.clear()
