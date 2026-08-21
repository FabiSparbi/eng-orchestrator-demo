"""Mocked parametric geometry tools.

Three tools with deliberately different risk profiles:

  * `propose_geometry_change` -- read-only. Recommends a parametric change for
    an agent-fixable region and states plainly which regions it cannot touch.
    Safe to call freely; recommending is not changing, so NO approval gate.

  * `apply_modification_workflow` -- the only tool where the AGENT mutates
    design state. Registered with approval_mode="always_require", so the run
    suspends and DevUI renders an Approve/Reject prompt before it executes.
    This is the concrete implementation of the brief's rule that every geometry
    change must be user-approved.

  * `record_manual_cad_rework` -- the human half. The engineer reworks the
    regions the agent could not, in CATIA, and reports back. This mutates state
    too, but it is NOT gated: the gate exists to stop an agent changing
    geometry unsupervised, and gating this would ask the engineer to approve
    their own report of work they have already done.

WHAT THE AGENT CAN AND CANNOT DO
--------------------------------
The Geometry Agent only performs parametric changes on named CATIA features --
opening up a hole radius, adjusting a fillet. It cannot reshape a flange,
re-profile a draw bead or move a trim line; those need a CAD engineer. The
simulation marks each region accordingly and this module refuses anything else.

PRODUCTION SWAP-IN: `apply_modification_workflow` becomes a call into the real
parametric CAD API (a CATIA/NX automation service or a PLM change workflow),
returning the real job id and model revision. The approval gate stays put.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mock_data import digital_twin as twin

# Parametric operations the agent supports, and how much severity each removes.
# A supported op resolves its target region in one approved step -- a hole that
# is too small for the local strain is fixed by making it big enough, not by
# three successive nudges.
_SUPPORTED_OPS = {
    "increase_hole_radius": 0.45,
    "increase_fillet_radius": 0.45,
}


def propose_geometry_change(part_id: str, analysis_type: str = "stamping", region_id: str | None = None) -> dict[str, Any]:
    """Recommend a parametric geometry change for a critical region.

    READ-ONLY: produces a recommendation only. Nothing is changed and no
    approval is needed. Applying it requires `apply_modification_workflow`.

    The proposal covers only regions the agent can actually fix. Regions needing
    CAD engineer rework are listed separately under `outOfScope` so they are
    handed over rather than silently dropped.

    Args:
        part_id: Part number, e.g. "10A.507.109".
        analysis_type: Which analysis flagged the problem. Defaults to "stamping".
        region_id: Optional specific region, e.g. "R-STM-01". If omitted, the
            worst agent-fixable region is used.

    Returns:
        The proposed operation with its parameters and expected effect, plus the
        regions that are out of scope for the agent.
    """
    part_id = part_id.strip().upper()
    analysis_type = analysis_type.strip().lower()
    if analysis_type not in twin.ANALYSIS_TYPES:
        return {"error": f"Unknown analysis type '{analysis_type}'. Expected one of {list(twin.ANALYSIS_TYPES)}."}

    fixable = twin.agent_fixable_areas(part_id, analysis_type)
    out_of_scope = twin.engineer_only_areas(part_id, analysis_type)
    handover = [
        {
            "regionId": a["regionId"],
            "regionType": a["regionType"],
            "location": a["location"],
            "severity": a["severity"],
            "suggestedFix": a["suggestedFix"],
        }
        for a in out_of_scope
    ]

    if not fixable:
        return {
            "partId": part_id,
            "analysisType": analysis_type,
            "proposal": None,
            "outOfScope": handover,
            "outOfScopeCount": len(handover),
            "message": (
                f"No agent-fixable regions remain for {analysis_type} on {part_id}."
                + (
                    f" {len(handover)} region(s) still need CAD engineer rework in CATIA."
                    if handover
                    else " No critical regions remain at all."
                )
            ),
        }

    target = next((a for a in fixable if a["regionId"] == region_id.strip().upper()), None) if region_id else None
    if target is None:
        if region_id:
            blocked = next((a for a in out_of_scope if a["regionId"] == region_id.strip().upper()), None)
            if blocked:
                return {
                    "partId": part_id,
                    "analysisType": analysis_type,
                    "proposal": None,
                    "outOfScope": handover,
                    "message": (
                        f"Region {blocked['regionId']} ({blocked['regionType']}) cannot be fixed by a parametric "
                        f"change. It needs CAD engineer rework: {blocked['suggestedFix']}."
                    ),
                }
        target = fixable[0]  # already sorted worst-first

    op = target["parametricOp"]
    effectiveness = _SUPPORTED_OPS.get(op, 0.0)

    return {
        "partId": part_id,
        "analysisType": analysis_type,
        "proposal": {
            "modificationType": op,
            "targetRegion": target["regionId"],
            "featureId": target["featureId"],
            "location": target["location"],
            "parameters": {"fromMm": target["currentValueMm"], "toMm": target["suggestedValueMm"]},
            "rationale": target["suggestedFix"],
            "issue": target["description"],
            "currentSeverity": target["severity"],
            "expectedSeverityAfter": round(max(0.0, target["severity"] - effectiveness), 3),
        },
        "outOfScope": handover,
        "outOfScopeCount": len(handover),
        "applied": False,
        "note": (
            "RECOMMENDATION ONLY -- nothing has been changed. Applying this requires "
            "apply_modification_workflow, which needs explicit user approval. The regions under "
            "'outOfScope' are outside what a parametric change can fix and need a CAD engineer."
        ),
    }


def apply_modification_workflow(
    part_id: str,
    modification_type: str,
    region_id: str,
    analysis_type: str = "stamping",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a parametric geometry change through the CAD workflow.

    MUTATES DESIGN STATE. This tool is registered with approval_mode
    "always_require": the run pauses and a human must approve it in DevUI
    before it executes. Do not describe a change as applied until this tool has
    actually returned a success result.

    Only works on regions the simulation marked `agentFixable`. Anything else is
    refused and must go to a CAD engineer.

    Args:
        part_id: Part number to modify, e.g. "10A.507.109".
        modification_type: The parametric operation, e.g. "increase_hole_radius".
        region_id: The critical region this addresses, e.g. "R-STM-01".
        analysis_type: Which analysis flagged it. Defaults to "stamping".
        parameters: Optional parameters, e.g. {"fromMm": 6.75, "toMm": 8.5}.

    Returns:
        The workflow result: status, job id, the region's new severity, and the
        updated model revision.
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

    effectiveness = _SUPPORTED_OPS.get(modification_type)
    if effectiveness is None:
        return {
            "partId": part_id,
            "status": "failure",
            "reason": (
                f"'{modification_type}' is not a supported parametric operation. "
                f"Supported: {sorted(_SUPPORTED_OPS)}. Anything else needs CAD engineer rework in CATIA."
            ),
        }

    area = twin.find_region(part_id, analysis_type, region_id) if region_id else None
    if area is None:
        return {
            "partId": part_id,
            "status": "failure",
            "reason": f"Region '{region_id}' not found in the {analysis_type} results for {part_id}.",
        }

    if not area["agentFixable"]:
        return {
            "partId": part_id,
            "status": "failure",
            "reason": (
                f"Region {area['regionId']} ({area['regionType']}) is not fixable by a parametric change. "
                f"It requires CAD engineer rework: {area['suggestedFix']}"
            ),
        }

    if area["severity"] < twin.SEVERITY_THRESHOLD:
        return {
            "partId": part_id,
            "status": "failure",
            "reason": f"Region {area['regionId']} is already below the critical threshold. Geometry was not modified.",
        }

    outcome = twin.resolve_region(part_id, analysis_type, area["regionId"], effectiveness)

    job_id = f"GEOM-{part_id.replace('.', '-')}-{len(twin.modification_history(part_id)) + 1:03d}"
    entry = {
        "jobId": job_id,
        "modificationType": modification_type,
        "analysisType": analysis_type,
        "regionId": area["regionId"],
        "featureId": area.get("featureId"),
        "parameters": parameters or {"fromMm": area.get("currentValueMm"), "toMm": area.get("suggestedValueMm")},
        "appliedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    revision = twin.record_modification(part_id, entry)

    remaining_fixable = len(twin.agent_fixable_areas(part_id, analysis_type))
    remaining_engineer = len(twin.engineer_only_areas(part_id, analysis_type))

    return {
        "partId": part_id,
        "status": "success",
        "jobId": job_id,
        "modificationType": modification_type,
        "featureId": area.get("featureId"),
        "targetRegion": area["regionId"],
        "parameters": entry["parameters"],
        "severityBefore": outcome["severityBefore"],
        "severityAfter": outcome["severityAfter"],
        "regionResolved": outcome["resolved"],
        "remainingAgentFixable": remaining_fixable,
        "remainingEngineerOnly": remaining_engineer,
        "modelRevision": f"rev-{revision:02d}",
        "workflow": "mock parametric CAD workflow (synthetic)",
        "note": "Geometry updated in the mock digital twin. Re-run the simulation to confirm the effect.",
    }


def record_manual_cad_rework(
    part_id: str,
    analysis_type: str = "stamping",
    region_ids: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Record that a CAD engineer has reworked regions by hand in CATIA.

    Call this ONLY when the engineer says they have made the changes. It records
    their report and updates the twin so the next simulation reflects the rework.
    It does not verify anything -- exactly like a real handover, the engineer's
    word is what moves the design forward.

    By default this covers the regions the Geometry Agent could not fix. Any
    region still open and agent-fixable is deliberately left alone, so it is not
    quietly absorbed into a manual handover.

    Args:
        part_id: Part number, e.g. "10A.507.109".
        analysis_type: Which analysis the rework addresses. Defaults to "stamping".
        region_ids: Optional specific regions the engineer says they fixed.
        note: Optional note from the engineer describing what they changed.

    Returns:
        Which regions were cleared and how many rework rounds have been recorded.
    """
    part_id = part_id.strip().upper()
    analysis_type = analysis_type.strip().lower() if analysis_type else None
    if analysis_type and analysis_type not in twin.ANALYSIS_TYPES:
        return {"error": f"Unknown analysis type '{analysis_type}'. Expected one of {list(twin.ANALYSIS_TYPES)}."}

    result = twin.record_manual_rework(part_id, analysis_type, region_ids, note)
    cleared = result["clearedRegions"]
    return {
        "partId": part_id,
        "status": "recorded",
        "clearedRegions": cleared,
        "clearedCount": len(cleared),
        "reworkRounds": result["reworkCount"],
        "note": note,
        "message": (
            f"Recorded manual CAD rework for {part_id}: {len(cleared)} region(s) marked as reworked. "
            "Re-run the simulation to confirm."
            if cleared
            else f"No open regions matched for {part_id}; nothing was recorded as reworked."
        ),
    }


def get_modification_history(part_id: str) -> dict[str, Any]:
    """List the geometry changes applied to a part, by the agent and by hand.

    Args:
        part_id: Part number, e.g. "10A.507.109".
    """
    part_id = part_id.strip().upper()
    history = twin.modification_history(part_id)
    manual = twin.manual_rework_history(part_id)
    return {
        "partId": part_id,
        "agentModifications": history,
        "agentModificationCount": len(history),
        "manualReworkRounds": manual,
        "manualReworkCount": len(manual),
        "currentRevision": f"rev-{len(history):02d}" if history else "rev-00 (baseline)",
    }
