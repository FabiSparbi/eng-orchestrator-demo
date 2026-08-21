"""Mocked CAE analyses over the shared digital twin.

The demo's focus is the STAMPING simulation: it flags many critical regions,
and each one is marked as either agent-fixable (a named parametric feature with
a parameter change as its fix) or engineer-only (needs CATIA rework by a human).
Stiffness and modal analyses are kept available but are not the demo path.

All analyses read the same in-memory part state, so once an approved geometry
modification or a reported manual rework improves a region, the next run sees it.

PRODUCTION SWAP-IN: each tool becomes a job submission to the real solver
(AutoForm / LS-DYNA for forming, Abaqus for structure) plus a results-parsing
step. The unified return schema is what the agents depend on, not the solver.
"""

from __future__ import annotations

from typing import Any

from mock_data import digital_twin as twin

_CRITERIA = {
    "stamping": "Thinning at or below the forming limit and no wrinkling or splitting risk.",
    "stiffness": "Max displacement at or below target under the nominal load case.",
    "modal": "First bending mode at or above the target frequency floor.",
}

_LABELS = {"stamping": "Stamping simulation", "stiffness": "Stiffness analysis", "modal": "Modal analysis"}


def _result(part_id: str, analysis_type: str) -> dict[str, Any]:
    """Assemble the unified analysis-result schema for one analysis type."""
    part_id = part_id.strip().upper()
    state = twin.get_part_state(part_id)
    block = state["analyses"][analysis_type]
    areas = twin.critical_areas(part_id, analysis_type)
    fixable = [a for a in areas if a["agentFixable"]]
    engineer_only = [a for a in areas if not a["agentFixable"]]
    metrics = {k: twin.jitter(v) if isinstance(v, (int, float)) else v for k, v in block["metrics"].items()}

    status = "pass" if not areas else "fail"
    label = _LABELS[analysis_type]

    if status == "pass":
        summary = f"{label} PASSED for {part_id}: no critical regions remain."
    else:
        summary = (
            f"{label} FAILED for {part_id}: {len(areas)} critical region(s). "
            f"{len(fixable)} can be fixed by the Geometry Agent as a parametric change; "
            f"{len(engineer_only)} need CAD engineer rework."
        )

    return {
        "partId": part_id,
        "analysisType": analysis_type,
        "status": status,
        "metrics": metrics,
        "criticalAreas": areas,
        "criticalAreaCount": len(areas),
        "agentFixableCount": len(fixable),
        "agentFixableRegions": [a["regionId"] for a in fixable],
        "engineerOnlyCount": len(engineer_only),
        "engineerOnlyRegions": [a["regionId"] for a in engineer_only],
        "maxSeverity": twin.max_severity(part_id, analysis_type),
        "severityThreshold": twin.SEVERITY_THRESHOLD,
        "passCriteria": _CRITERIA[analysis_type],
        "modificationsApplied": len(state["modifications"]),
        "manualReworkRounds": len(state["manualRework"]),
        "summary": summary,
        "solver": "mock CAE solver (synthetic results)",
    }


def run_stamping_simulation(part_id: str) -> dict[str, Any]:
    """Run the stamping (sheet-metal forming) simulation on a part.

    This is the primary analysis for B-pillars: it predicts thinning, splitting
    and wrinkling during the draw, and reports every critical region found.

    Each critical region is marked `agentFixable`. True means it is attached to
    a named parametric feature and the recommended fix is a parameter change
    the Geometry Agent can apply (for example, opening up a hole radius). False
    means it needs a CAD engineer to rework the part in CATIA.

    Args:
        part_id: Part number to analyse, e.g. "10A.507.109".

    Returns:
        Unified analysis result: pass/fail status, forming metrics, and the
        critical regions split into agent-fixable and engineer-only.
    """
    return _result(part_id, "stamping")


def run_stiffness_analysis(part_id: str) -> dict[str, Any]:
    """Run a static stiffness analysis on a part.

    Args:
        part_id: Part number to analyse, e.g. "10A.507.109".
    """
    return _result(part_id, "stiffness")


def run_modal_analysis(part_id: str) -> dict[str, Any]:
    """Run a modal (natural frequency) analysis on a part.

    Args:
        part_id: Part number to analyse, e.g. "10A.507.109".
    """
    return _result(part_id, "modal")


def get_analysis_overview(part_id: str) -> dict[str, Any]:
    """Summarise all analyses for a part in one call.

    Args:
        part_id: Part number to summarise, e.g. "10A.507.109".
    """
    part_id = part_id.strip().upper()
    results = {a: _result(part_id, a) for a in twin.ANALYSIS_TYPES}
    remaining = sum(r["criticalAreaCount"] for r in results.values())
    fixable = sum(r["agentFixableCount"] for r in results.values())
    return {
        "partId": part_id,
        "overallStatus": "pass" if remaining == 0 else "fail",
        "totalCriticalAreas": remaining,
        "totalAgentFixable": fixable,
        "totalEngineerOnly": remaining - fixable,
        "byAnalysis": {
            a: {
                "status": r["status"],
                "criticalAreas": r["criticalAreaCount"],
                "agentFixable": r["agentFixableCount"],
                "engineerOnly": r["engineerOnlyCount"],
                "maxSeverity": r["maxSeverity"],
            }
            for a, r in results.items()
        },
        "modificationsApplied": len(twin.get_part_state(part_id)["modifications"]),
        "manualReworkRounds": len(twin.get_part_state(part_id)["manualRework"]),
    }


def list_engineer_rework_items(part_id: str, analysis_type: str = "stamping") -> dict[str, Any]:
    """List the critical regions a CAD engineer has to rework by hand.

    Use this to hand over: it returns the regions the Geometry Agent cannot
    resolve, each with its location and the suggested fix, so the engineer has
    an actionable worklist.

    Args:
        part_id: Part number, e.g. "10A.507.109".
        analysis_type: Which analysis to hand over. Defaults to "stamping".
    """
    part_id = part_id.strip().upper()
    analysis_type = analysis_type.strip().lower()
    if analysis_type not in twin.ANALYSIS_TYPES:
        return {"error": f"Unknown analysis type '{analysis_type}'. Expected one of {list(twin.ANALYSIS_TYPES)}."}

    items = twin.engineer_only_areas(part_id, analysis_type)
    return {
        "partId": part_id,
        "analysisType": analysis_type,
        "itemCount": len(items),
        "worklist": [
            {
                "regionId": a["regionId"],
                "regionType": a["regionType"],
                "location": a["location"],
                "severity": a["severity"],
                "issue": a["description"],
                "suggestedFix": a["suggestedFix"],
            }
            for a in items
        ],
        "note": (
            "These require opening the part in CATIA. The suggested fixes are advisory -- "
            "the CAD engineer should judge whether each is sensible before applying it."
        ),
    }
