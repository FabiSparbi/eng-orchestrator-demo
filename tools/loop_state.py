"""Deterministic loop / session state for the design-iteration loop.

The orchestrator must NOT be trusted to count iterations in its own head across
turns -- it will drift. These tools own the count and the continue/stop/wait
decision in code.

THREE OUTCOMES, NOT TWO
-----------------------
A stamping simulation flags regions the agent can fix and regions only a CAD
engineer can. So "stop" is not one state but two, and the orchestrator must be
able to tell them apart:

  * shouldContinue = True                  -> agent-fixable work remains; loop.
  * blockedOn = "manual_cad_rework"        -> nothing left the agent can fix,
                                              but critical regions remain. The
                                              loop is NOT over: prompt the
                                              engineer to rework them in CATIA
                                              and report back.
  * terminated = True                      -> the loop is genuinely finished;
                                              `terminationReason` says why.

Termination reasons implement the brief's four conditions:
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
DEFAULT_ANALYSIS = "stamping"

_SESSIONS: dict[str, dict[str, Any]] = {}


def _session(part_id: str) -> dict[str, Any] | None:
    return _SESSIONS.get(part_id.strip().upper())


def start_design_loop(
    part_id: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    goal: str | None = None,
    analysis_type: str | None = DEFAULT_ANALYSIS,
) -> dict[str, Any]:
    """Open a design-iteration session for a part and reset its counter.

    Call this once, before the first simulate -> recommend -> approve -> apply
    cycle. Calling it again on the same part restarts the loop from iteration 0.

    Args:
        part_id: Part number under design, e.g. "10A.507.109".
        max_iterations: Hard cap on agent iterations. Default 5.
        goal: Optional plain-language statement of what the loop is for.
        analysis_type: Which analysis the loop is responsible for. Defaults to
            "stamping". Pass None to require every analysis to pass.

    Returns:
        The new session state.
    """
    part_id = part_id.strip().upper()
    scope = analysis_type.strip().lower() if analysis_type else None
    if scope and scope not in twin.ANALYSIS_TYPES:
        return {"error": f"Unknown analysis type '{analysis_type}'. Expected one of {list(twin.ANALYSIS_TYPES)}."}

    # IDEMPOTENT ON PURPOSE.
    # A model that re-issues this call -- because it re-read its instructions, or
    # lost track of what it had already done -- used to silently reset the
    # counter and wipe the history. The iteration count could then never reach
    # the cap, so `max_iterations_reached` could never fire and the loop was
    # effectively unbounded. Now a repeat is a no-op that reports the live state
    # and says what to do instead, which both preserves progress and gives the
    # model an unambiguous signal to move on.
    existing = _SESSIONS.get(part_id)
    if existing and existing["active"]:
        return {
            "partId": part_id,
            "alreadyRunning": True,
            "iteration": existing["iteration"],
            "maxIterations": existing["maxIterations"],
            "analysisType": existing["analysisType"],
            "message": (
                f"A design loop for {part_id} is already running (iteration "
                f"{existing['iteration']} of {existing['maxIterations']}). Nothing was reset."
            ),
            "nextAction": (
                "Do NOT call start_design_loop again for this part. Call check_loop_status "
                "to find out what to do next."
            ),
        }

    _SESSIONS[part_id] = {
        "partId": part_id,
        "iteration": 0,
        "maxIterations": int(max_iterations),
        "goal": goal,
        "analysisType": scope,
        "active": True,
        "terminationReason": None,
        "startedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history": [],
    }
    scope_text = f" scoped to {scope}" if scope else " across all analyses"
    return {
        "partId": part_id,
        "alreadyRunning": False,
        "iteration": 0,
        "maxIterations": int(max_iterations),
        "analysisType": scope,
        "goal": goal,
        "message": f"Design loop started for {part_id}{scope_text} (max {max_iterations} iterations).",
        "nextAction": (
            "Loop is open. Run the simulation for this part, then call check_loop_status. "
            "Do not call start_design_loop again for this part."
        ),
    }


def record_iteration(part_id: str, action: str, outcome: str, detail: str | None = None) -> dict[str, Any]:
    """Record one completed design iteration and increment the counter.

    Call this after each apply-and-re-simulate cycle, not after every tool call.

    Args:
        part_id: Part number under design.
        action: What was done, e.g. "increase_hole_radius on R-STM-01".
        outcome: "success" or "failure".
        detail: Optional extra context, e.g. the re-simulation verdict.
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


def record_manual_rework_round(part_id: str, note: str | None = None) -> dict[str, Any]:
    """Log that the engineer reported doing manual CAD rework in this loop.

    This records the handover in the loop history. It does NOT change the
    simulation state -- `record_manual_cad_rework` in the geometry tools does
    that. Call this alongside it so the loop history tells the whole story.

    Args:
        part_id: Part number under design.
        note: What the engineer said they changed.
    """
    part_id = part_id.strip().upper()
    session = _session(part_id)
    if session is None:
        return {"error": f"No active design loop for {part_id}."}
    session["history"].append(
        {
            "iteration": session["iteration"],
            "action": "manual CAD rework by engineer",
            "outcome": "reported",
            "detail": note,
            "atUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    return {"partId": part_id, "recorded": True, "note": note}


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
        "message": f"Design loop for {part_id} cancelled by user" + (f": {reason}" if reason else "."),
    }


def check_loop_status(part_id: str) -> dict[str, Any]:
    """Decide -- in code, not by memory -- what the design loop does next.

    Returns one of three verdicts: keep looping, wait for the engineer's manual
    CAD rework, or stop. Obey it rather than judging for yourself, and never
    count iterations in your head.

    Args:
        part_id: Part number under design, e.g. "10A.507.109".

    Returns:
        `shouldContinue`, `blockedOn` and `terminated` with the reason, plus the
        current counts of agent-fixable and engineer-only regions.
    """
    part_id = part_id.strip().upper()
    session = _session(part_id)
    if session is None:
        # Open one implicitly rather than erroring. Requiring start_design_loop
        # first makes the tools order-dependent, and an order-dependent tool set
        # is one a model can get stuck on -- which is exactly how the repeated
        # start_design_loop calls arose. Any entry point now works.
        start_design_loop(part_id)
        session = _session(part_id)
        assert session is not None

    overview = get_analysis_overview(part_id)

    # Already-terminated loops (user cancel / workflow failure) stay terminated.
    if not session["active"]:
        return _verdict(session, overview, should_continue=False, reason=session["terminationReason"] or "stopped")

    # Condition 1: nothing critical left in this loop's scope.
    if _counts(session, overview)["open"] == 0:
        session["active"] = False
        session["terminationReason"] = "converged"
        return _verdict(session, overview, should_continue=False, reason="converged")

    # Condition 2: iteration cap reached.
    if session["iteration"] >= session["maxIterations"]:
        session["active"] = False
        session["terminationReason"] = "max_iterations_reached"
        return _verdict(session, overview, should_continue=False, reason="max_iterations_reached")

    # Not terminated: the agent has nothing left it can legitimately fix, but
    # critical regions remain. Hand over to the CAD engineer and wait.
    if _counts(session, overview)["fixable"] == 0:
        return _verdict(session, overview, should_continue=False, reason=None, blocked_on="manual_cad_rework")

    return _verdict(session, overview, should_continue=True, reason=None)


def _counts(session: dict[str, Any], overview: dict[str, Any]) -> dict[str, int]:
    """Open / agent-fixable / engineer-only counts within the loop's scope."""
    scope = session.get("analysisType")
    if scope:
        block = overview["byAnalysis"][scope]
        return {"open": block["criticalAreas"], "fixable": block["agentFixable"], "engineer": block["engineerOnly"]}
    return {
        "open": overview["totalCriticalAreas"],
        "fixable": overview["totalAgentFixable"],
        "engineer": overview["totalEngineerOnly"],
    }


_MESSAGES = {
    "converged": "All critical regions resolved -- design converged. Termination condition: no critical regions remain.",
    "max_iterations_reached": "Iteration cap reached with critical regions still open. Termination condition: max iterations reached.",
    "user_cancelled": "Loop stopped at the user's request. Termination condition: user cancelled.",
    "geometry_workflow_failed": "The geometry workflow reported a failure. Termination condition: geometry workflow failed.",
    "stopped": "Loop is no longer active.",
}


def _verdict(
    session: dict[str, Any],
    overview: dict[str, Any],
    should_continue: bool,
    reason: str | None,
    blocked_on: str | None = None,
) -> dict[str, Any]:
    scope = session.get("analysisType")
    counts = _counts(session, overview)
    terminated = reason is not None

    if reason:
        message = _MESSAGES.get(reason, f"Loop stopped: {reason}.")
        next_action = "Report the outcome to the engineer and summarise what changed."
    elif blocked_on == "manual_cad_rework":
        message = (
            f"No agent-fixable regions remain, but {counts['engineer']} critical region(s) still need "
            "CAD engineer rework. The loop is waiting for the engineer, not finished."
        )
        next_action = (
            "Hand the remaining regions to the engineer with their suggested fixes, and ask them to make "
            "the changes in CATIA and confirm when done. When they confirm, record the rework and re-run "
            "the simulation."
        )
    else:
        message = (
            f"Loop continues: {counts['fixable']} agent-fixable region(s) open"
            + (f" for {scope}" if scope else "")
            + f", iteration {session['iteration']} of {session['maxIterations']}."
        )
        next_action = "Propose a change for the worst agent-fixable region, get approval, apply, re-simulate."

    return {
        "partId": session["partId"],
        "analysisScope": scope or "all",
        "shouldContinue": should_continue,
        "terminated": terminated,
        "blockedOn": blocked_on,
        "iteration": session["iteration"],
        "maxIterations": session["maxIterations"],
        "iterationsRemaining": max(0, session["maxIterations"] - session["iteration"]),
        "terminationReason": reason,
        "criticalAreasRemaining": counts["open"],
        "agentFixableRemaining": counts["fixable"],
        "engineerOnlyRemaining": counts["engineer"],
        "criticalAreasAllAnalyses": overview["totalCriticalAreas"],
        "modificationsApplied": overview["modificationsApplied"],
        "manualReworkRounds": overview["manualReworkRounds"],
        "message": message,
        "nextAction": next_action,
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
        "analysisScope": session.get("analysisType") or "all",
        "iterationsCompleted": session["iteration"],
        "maxIterations": session["maxIterations"],
        "active": session["active"],
        "terminationReason": session["terminationReason"],
        "history": session["history"],
    }


def reset_all_sessions() -> None:
    """Clear all loop sessions (used by tests)."""
    _SESSIONS.clear()
