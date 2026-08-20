"""Deterministic loop / session state for the design-iteration loop.

The orchestrator must NOT be trusted to count iterations in its own head across
turns -- it will drift. These tools own the count and the termination decision
in code, implementing the brief's four termination conditions exactly:

  1. no critical regions remain            -> "converged"
  2. max iterations reached (default 5)    -> "max_iterations_reached"
  3. user cancels                          -> "user_cancelled"
  4. geometry workflow reports failure     -> "geometry_workflow_failed"

=============================================================================
PERSISTENCE NOTE: like the digital twin, sessions live in a module-level dict
and reset when the process exits. Swap `_SESSIONS` for a file/SQLite/Redis
store behind these same functions if loops must survive restarts.
=============================================================================
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mock_data import digital_twin as twin
from tools.simulation_tools import get_analysis_overview

DEFAULT_MAX_ITERATIONS = 5

_SESSIONS: dict[str, dict[str, Any]] = {}


def _session(part_id: str) -> dict[str, Any] | None:
    return _SESSIONS.get(part_id.strip().upper())


def start_design_loop(part_id: str, max_iterations: int = DEFAULT_MAX_ITERATIONS, goal: str | None = None) -> dict[str, Any]:
    """Open a design-iteration session for a part and reset its counter.

    Call this once, before the first simulate -> recommend -> approve -> apply
    cycle. Calling it again on the same part restarts the loop from iteration 0.

    Args:
        part_id: Part number under design, e.g. "BR-3310".
        max_iterations: Hard cap on iterations before the loop stops. Default 5.
        goal: Optional plain-language statement of what the loop is trying to
            achieve, echoed back in status reports.

    Returns:
        The new session state.
    """
    part_id = part_id.strip().upper()
    _SESSIONS[part_id] = {
        "partId": part_id,
        "iteration": 0,
        "maxIterations": int(max_iterations),
        "goal": goal,
        "active": True,
        "terminationReason": None,
        "startedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history": [],
    }
    return {**_SESSIONS[part_id], "message": f"Design loop started for {part_id} (max {max_iterations} iterations)."}


def record_iteration(part_id: str, action: str, outcome: str, detail: str | None = None) -> dict[str, Any]:
    """Record one completed design iteration and increment the counter.

    Call this after each apply-and-re-simulate cycle, not after every tool call.

    Args:
        part_id: Part number under design.
        action: What was done this iteration, e.g.
            "increase_fillet_radius on R-STP-01".
        outcome: Result of the iteration -- use "success" or "failure".
        detail: Optional extra context, e.g. the re-simulation verdict.

    Returns:
        The updated session, including the new iteration count.
    """
    part_id = part_id.strip().upper()
    session = _session(part_id)
    if session is None:
        return {"error": f"No active design loop for {part_id}. Call start_design_loop first."}

    session["iteration"] += 1
    session["history"].append(
        {
            "iteration": session["iteration"],
            "action": action,
            "outcome": outcome,
            "detail": detail,
            "atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )

    # Termination condition 4: the geometry workflow reported failure.
    if outcome.strip().lower() == "failure":
        session["active"] = False
        session["terminationReason"] = "geometry_workflow_failed"

    return {
        "partId": part_id,
        "iteration": session["iteration"],
        "maxIterations": session["maxIterations"],
        "active": session["active"],
        "terminationReason": session["terminationReason"],
    }


def cancel_design_loop(part_id: str, reason: str | None = None) -> dict[str, Any]:
    """Stop a design loop because the user asked to stop.

    Args:
        part_id: Part number under design.
        reason: Optional reason given by the user.
    """
    part_id = part_id.strip().upper()
    session = _session(part_id)
    if session is None:
        return {"error": f"No active design loop for {part_id}."}
    session["active"] = False
    session["terminationReason"] = "user_cancelled"
    session["cancellationReason"] = reason
    return {
        "partId": part_id,
        "active": False,
        "terminationReason": "user_cancelled",
        "iterationsCompleted": session["iteration"],
        "message": f"Design loop for {part_id} cancelled by user"
        + (f": {reason}" if reason else "."),
    }


def check_loop_status(part_id: str) -> dict[str, Any]:
    """Decide -- in code, not by memory -- whether the design loop continues.

    Evaluates all four termination conditions against the current session and
    the live simulation state, and returns an explicit continue/stop verdict.
    Call this before starting each new iteration.

    Args:
        part_id: Part number under design, e.g. "BR-3310".

    Returns:
        `shouldContinue` plus the termination reason and a human-readable
        message naming which condition fired.
    """
    part_id = part_id.strip().upper()
    session = _session(part_id)
    if session is None:
        return {
            "partId": part_id,
            "shouldContinue": False,
            "terminationReason": "no_active_loop",
            "message": f"No design loop is running for {part_id}. Call start_design_loop to begin one.",
        }

    overview = get_analysis_overview(part_id)

    # Already-terminated loops (user cancel / workflow failure) stay terminated.
    if not session["active"]:
        reason = session["terminationReason"] or "stopped"
        return _verdict(session, overview, False, reason)

    # Condition 1: nothing critical left anywhere.
    if overview["totalCriticalAreas"] == 0:
        session["active"] = False
        session["terminationReason"] = "converged"
        return _verdict(session, overview, False, "converged")

    # Condition 2: iteration cap reached.
    if session["iteration"] >= session["maxIterations"]:
        session["active"] = False
        session["terminationReason"] = "max_iterations_reached"
        return _verdict(session, overview, False, "max_iterations_reached")

    return _verdict(session, overview, True, None)


_MESSAGES = {
    "converged": "All critical regions resolved -- design converged. Termination condition: no critical regions remain.",
    "max_iterations_reached": "Iteration cap reached with critical regions still open. Termination condition: max iterations reached.",
    "user_cancelled": "Loop stopped at the user's request. Termination condition: user cancelled.",
    "geometry_workflow_failed": "The geometry workflow reported a failure. Termination condition: geometry workflow failed.",
    "stopped": "Loop is no longer active.",
}


def _verdict(session: dict[str, Any], overview: dict[str, Any], should_continue: bool, reason: str | None) -> dict[str, Any]:
    return {
        "partId": session["partId"],
        "shouldContinue": should_continue,
        "iteration": session["iteration"],
        "maxIterations": session["maxIterations"],
        "iterationsRemaining": max(0, session["maxIterations"] - session["iteration"]),
        "terminationReason": reason,
        "criticalAreasRemaining": overview["totalCriticalAreas"],
        "byAnalysis": overview["byAnalysis"],
        "modificationsApplied": overview["modificationsApplied"],
        "message": _MESSAGES.get(reason or "", "")
        or f"Loop continues: {overview['totalCriticalAreas']} critical region(s) still open, "
        f"iteration {session['iteration']} of {session['maxIterations']}.",
    }


def get_loop_history(part_id: str) -> dict[str, Any]:
    """Return the full iteration history for a part's design loop.

    Args:
        part_id: Part number under design.
    """
    session = _session(part_id)
    if session is None:
        return {"error": f"No design loop found for {part_id.strip().upper()}."}
    return {
        "partId": session["partId"],
        "goal": session["goal"],
        "iterationsCompleted": session["iteration"],
        "maxIterations": session["maxIterations"],
        "active": session["active"],
        "terminationReason": session["terminationReason"],
        "history": session["history"],
    }


def reset_all_sessions() -> None:
    """Clear all loop sessions (used by tests)."""
    _SESSIONS.clear()
