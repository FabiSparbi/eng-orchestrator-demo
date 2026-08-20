"""Mocked parametric geometry tools.

Two tools with deliberately different risk profiles:

  * `propose_geometry_change` -- read-only. Recommends a modification. Safe to
    call freely; recommending is not changing, so it carries NO approval gate.

  * `apply_modification_workflow` -- the only tool in this demo that mutates
    design state. It is registered on the Geometry Agent with
    approval_mode="always_require", so the Agent Framework suspends the run and
    DevUI renders an Approve/Reject prompt before it executes. This is the
    concrete implementation of the brief's rule that every geometry change must
    be user-approved.

PRODUCTION SWAP-IN: `apply_modification_workflow` becomes a call into the real
parametric CAD/workflow API (e.g. a CATIA/NX automation service or a PLM change
workflow), returning the real job id and updated model revision. The approval
gate stays exactly where it is.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from mock_data import digital_twin as twin

_rng = random.Random(90210)

# How much severity one applied modification removes, by modification type.
# Chosen so a typical demo part converges in 2-3 approved iterations.
_EFFECTIVENESS = {
    "increase_rib_thickness": 0.30,
    "add_stiffening_rib": 0.34,
    "increase_fillet_radius": 0.32,
    "add_draw_bead": 0.30,
    "increase_wall_thickness": 0.28,
    "add_relief_pocket": 0.22,
    "reposition_boss": 0.20,
}

# Which modification is the sensible answer to which kind of critical region.
_PLAYBOOK = {
    "stiffness": ("increase_rib_thickness", "Thicken the rib along the primary load path to cut displacement."),
    "modal": ("add_stiffening_rib", "Add a stiffening rib at the free edge to raise the first bending mode."),
    "stampability": ("increase_fillet_radius", "Open up the draw radius to bring thinning back under the forming limit."),
}


def propose_geometry_change(part_id: str, analysis_type: str, region_id: str | None = None) -> dict[str, Any]:
    """Recommend a geometry modification to resolve a critical region.

    READ-ONLY: this only produces a recommendation. Nothing in the design is
    changed and no approval is needed. Applying the recommendation requires
    `apply_modification_workflow`, which is user-approved separately.

    Args:
        part_id: Part number, e.g. "BR-3310".
        analysis_type: Which analysis flagged the problem -- "stiffness",
            "modal" or "stampability".
        region_id: Optional specific region to target, e.g. "R-STP-01". If
            omitted, the worst remaining region for that analysis is used.

    Returns:
        The proposed modification type, parameters, target region, expected
        effect and the explicit note that it is not yet applied.
    """
    part_id = part_id.strip().upper()
    analysis_type = analysis_type.strip().lower()
    if analysis_type not in twin.ANALYSIS_TYPES:
        return {"error": f"Unknown analysis type '{analysis_type}'. Expected one of {list(twin.ANALYSIS_TYPES)}."}

    areas = twin.critical_areas(part_id, analysis_type)
    if not areas:
        return {
            "partId": part_id,
            "analysisType": analysis_type,
            "proposal": None,
            "message": f"No critical regions remain for {analysis_type} on {part_id}; no modification needed.",
        }

    target = next((a for a in areas if a["regionId"] == region_id), None) if region_id else None
    if target is None:
        target = max(areas, key=lambda a: a["severity"])

    mod_type, rationale = _PLAYBOOK[analysis_type]
    effectiveness = _EFFECTIVENESS[mod_type]

    parameters = {
        "increase_rib_thickness": {"fromMm": 3.0, "toMm": 4.5},
        "add_stiffening_rib": {"ribHeightMm": 12.0, "ribThicknessMm": 3.0, "lengthMm": 85.0},
        "increase_fillet_radius": {"fromMm": 6.0, "toMm": 9.0},
    }[mod_type]

    return {
        "partId": part_id,
        "analysisType": analysis_type,
        "proposal": {
            "modificationType": mod_type,
            "targetRegion": target["regionId"],
            "location": target["location"],
            "parameters": parameters,
            "rationale": rationale,
            "currentSeverity": target["severity"],
            "expectedSeverityAfter": round(max(0.0, target["severity"] - effectiveness), 3),
            "expectedMassChangeKg": round(_rng.uniform(0.02, 0.09), 3),
        },
        "applied": False,
        "note": (
            "RECOMMENDATION ONLY -- nothing has been changed. Applying this requires "
            "apply_modification_workflow, which needs explicit user approval."
        ),
    }


def apply_modification_workflow(
    part_id: str,
    modification_type: str,
    analysis_type: str,
    region_id: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a geometry modification through the parametric CAD workflow.

    MUTATES DESIGN STATE. This tool is registered with approval_mode
    "always_require": the run pauses and a human must approve it in DevUI
    before it executes. Do not describe a change as applied until this tool has
    actually returned a success result.

    Args:
        part_id: Part number to modify, e.g. "BR-3310".
        modification_type: The modification to run, e.g.
            "increase_rib_thickness", "add_stiffening_rib",
            "increase_fillet_radius", "add_draw_bead".
        analysis_type: The analysis whose critical region this addresses --
            "stiffness", "modal" or "stampability".
        region_id: Optional specific region to modify, e.g. "R-STP-01".
        parameters: Optional modification parameters, e.g. {"fromMm": 6, "toMm": 9}.

    Returns:
        The workflow result: status, job id, regions improved, remaining
        severity per analysis, and the updated model revision.
    """
    part_id = part_id.strip().upper()
    analysis_type = analysis_type.strip().lower()
    modification_type = modification_type.strip().lower()

    if analysis_type not in twin.ANALYSIS_TYPES:
        return {
            "partId": part_id,
            "status": "failure",
            "reason": f"Unknown analysis type '{analysis_type}'. Expected one of {list(twin.ANALYSIS_TYPES)}.",
        }

    effectiveness = _EFFECTIVENESS.get(modification_type)
    if effectiveness is None:
        return {
            "partId": part_id,
            "status": "failure",
            "reason": (
                f"Modification type '{modification_type}' is not supported by the parametric workflow. "
                f"Supported: {sorted(_EFFECTIVENESS)}."
            ),
        }

    # Guard: is there actually an open critical region for this target? Check
    # the TARGETED analysis specifically -- an applied change always nudges the
    # other analyses a little through coupling, so "did anything change at all"
    # is not a valid test of whether this modification had a job to do.
    open_areas = twin.critical_areas(part_id, analysis_type)
    if region_id:
        open_areas = [a for a in open_areas if a["regionId"] == region_id.strip().upper()]

    before = {a: twin.max_severity(part_id, a) for a in twin.ANALYSIS_TYPES}

    if not open_areas:
        # Nothing was left to fix -- report failure honestly rather than
        # claiming a successful change. This is also termination condition 4
        # ("geometry workflow reports failure") for the orchestrator's loop.
        return {
            "partId": part_id,
            "status": "failure",
            "reason": (
                f"No open critical region matched (analysis='{analysis_type}'"
                + (f", region='{region_id}'" if region_id else "")
                + "). Geometry was not modified."
            ),
            "severityBefore": before,
        }

    outcome = twin.apply_severity_reduction(
        part_id, effectiveness, target_analysis=analysis_type, target_region=region_id
    )

    job_id = f"GEOM-{part_id}-{len(twin.modification_history(part_id)) + 1:03d}"
    entry = {
        "jobId": job_id,
        "modificationType": modification_type,
        "analysisType": analysis_type,
        "regionId": region_id,
        "parameters": parameters or {},
        "appliedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "regionsImproved": outcome["regionsImproved"],
    }
    revision = twin.record_modification(part_id, entry)

    return {
        "partId": part_id,
        "status": "success",
        "jobId": job_id,
        "modificationType": modification_type,
        "targetRegion": region_id,
        "parameters": parameters or {},
        "regionsImproved": outcome["regionsImproved"],
        "severityBefore": before,
        "severityAfter": outcome["remainingSeverity"],
        "modelRevision": f"rev-{revision:02d}",
        "workflow": "mock parametric CAD workflow (synthetic)",
        "note": "Geometry updated in the mock digital twin. Re-run the relevant analysis to confirm convergence.",
    }


def get_modification_history(part_id: str) -> dict[str, Any]:
    """List the geometry modifications already applied to a part.

    Args:
        part_id: Part number, e.g. "BR-3310".
    """
    part_id = part_id.strip().upper()
    history = twin.modification_history(part_id)
    return {
        "partId": part_id,
        "modificationCount": len(history),
        "modifications": history,
        "currentRevision": f"rev-{len(history):02d}" if history else "rev-00 (baseline)",
    }
