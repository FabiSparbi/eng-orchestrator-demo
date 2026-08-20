"""Mocked CAE analyses over the shared digital twin.

All three tools read the same in-memory part state (mock_data/digital_twin.py),
so once an approved geometry modification reduces a region's severity, the next
analysis call sees the improvement. That is what makes the demo's CAD/CAE loop
visibly converge rather than repeat forever.

PRODUCTION SWAP-IN: each tool becomes a job submission to the real solver
(Abaqus / LS-DYNA / AutoForm for the stamping check) plus a results-parsing
step; the unified return schema below is what the agents depend on, not the
solver.
"""

from __future__ import annotations

from typing import Any

from mock_data import digital_twin as twin

# Human-readable pass criteria per analysis, quoted back in the results so a
# reviewer can see what "pass" actually means.
_CRITERIA = {
    "stiffness": "Max displacement at or below target under the nominal load case.",
    "modal": "First bending mode at or above the target frequency floor.",
    "stampability": "Max thinning at or below the forming limit, no wrinkling risk.",
}


def _result(part_id: str, analysis_type: str) -> dict[str, Any]:
    """Assemble the unified analysis-result schema for one analysis type."""
    part_id = part_id.strip().upper()
    state = twin.get_part_state(part_id)
    block = state["analyses"][analysis_type]
    areas = twin.critical_areas(part_id, analysis_type)
    metrics = {k: twin.jitter(v) if isinstance(v, (int, float)) else v for k, v in block["metrics"].items()}

    status = "pass" if not areas else "fail"
    return {
        "partId": part_id,
        "analysisType": analysis_type,
        "status": status,
        "metrics": metrics,
        "criticalAreas": areas,
        "maxSeverity": twin.max_severity(part_id, analysis_type),
        "severityThreshold": twin.SEVERITY_THRESHOLD,
        "passCriteria": _CRITERIA[analysis_type],
        "modificationsApplied": len(state["modifications"]),
        "summary": (
            f"{analysis_type.capitalize()} analysis PASSED for {part_id}: no critical regions remain."
            if status == "pass"
            else f"{analysis_type.capitalize()} analysis FAILED for {part_id}: "
            f"{len(areas)} critical region(s), worst severity {twin.max_severity(part_id, analysis_type)}."
        ),
        "solver": "mock CAE solver (synthetic results)",
    }


def run_stiffness_analysis(part_id: str) -> dict[str, Any]:
    """Run a static stiffness analysis on a part.

    Args:
        part_id: Part number to analyse, e.g. "BR-3310".

    Returns:
        Unified analysis result: status (pass/fail), metrics, and any critical
        regions with their severity and location.
    """
    return _result(part_id, "stiffness")


def run_modal_analysis(part_id: str) -> dict[str, Any]:
    """Run a modal (natural frequency) analysis on a part.

    Args:
        part_id: Part number to analyse, e.g. "BR-3310".

    Returns:
        Unified analysis result: status (pass/fail), metrics, and any critical
        regions with their severity and location.
    """
    return _result(part_id, "modal")


def run_stampability_analysis(part_id: str) -> dict[str, Any]:
    """Run a sheet-metal formability (stampability) analysis on a part.

    Args:
        part_id: Part number to analyse, e.g. "BR-3310".

    Returns:
        Unified analysis result: status (pass/fail), metrics, and any critical
        regions with their severity and location.
    """
    return _result(part_id, "stampability")


def get_analysis_overview(part_id: str) -> dict[str, Any]:
    """Summarise all three analyses for a part in one call.

    Useful for the orchestrator to check whether any critical regions remain
    anywhere before deciding to end the design loop.

    Args:
        part_id: Part number to summarise, e.g. "BR-3310".
    """
    part_id = part_id.strip().upper()
    results = {a: _result(part_id, a) for a in twin.ANALYSIS_TYPES}
    remaining = sum(len(r["criticalAreas"]) for r in results.values())
    return {
        "partId": part_id,
        "overallStatus": "pass" if remaining == 0 else "fail",
        "totalCriticalAreas": remaining,
        "byAnalysis": {a: {"status": r["status"], "criticalAreas": len(r["criticalAreas"]), "maxSeverity": r["maxSeverity"]} for a, r in results.items()},
        "modificationsApplied": len(twin.get_part_state(part_id)["modifications"]),
    }
