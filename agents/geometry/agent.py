"""Geometry Agent -- recommends parametric changes and hands over the rest.

THIS AGENT DOES NOT APPLY GEOMETRY. It recommends; the Orchestrator executes the
approved change. That split is not cosmetic -- it is required for the approval
gate to work at all.

WHY (reproduced, see tests/test_approval_roundtrip.py)
-----------------------------------------------------
`agent.as_tool()` runs a sub-agent as a stateless function call. If a tool
inside the sub-agent requires approval, the framework raises
UserInputRequiredException and the sub-agent run is ABANDONED; the approval
request is re-tagged with the ORCHESTRATOR's call id and surfaced to the user.
When the user approves, the orchestrator re-invokes this agent as a tool -- a
fresh run with no memory of the pending approval -- so it proposes the same
change again and hits the gate again. The result is an infinite
approve -> re-ask loop in which the change is NEVER applied.

Putting the gated tool on the agent that owns the conversation fixes it: the
approval response then matches that agent's own function call and resumes it.

`record_manual_cad_rework` also changes state but is deliberately NOT gated: it
records what a CAD engineer says they already did by hand in CATIA. The gate
exists to stop an agent changing geometry unsupervised; asking the engineer to
approve their own report would be noise. Being ungated, it round-trips through
as_tool() without trouble.
"""

from __future__ import annotations

from agent_framework import Agent, tool

from common.llm_client import get_shared_chat_client
from tools.geometry_tools import (
    get_modification_history,
    propose_geometry_change,
    record_manual_cad_rework,
)

# Recommending is not changing -- no approval gate on the proposal tool.
propose_tool = tool(
    propose_geometry_change,
    name="propose_geometry_change",
    description=(
        "Recommend a parametric change for an agent-fixable critical region, and "
        "list the regions that need CAD engineer rework instead. Read-only."
    ),
)

# The human half of the loop: the engineer's own report of manual CATIA work.
manual_rework_tool = tool(
    record_manual_cad_rework,
    name="record_manual_cad_rework",
    description=(
        "Record that the CAD engineer has reworked the remaining critical regions "
        "by hand in CATIA. Call only when the engineer says they have done it."
    ),
)

history_tool = tool(
    get_modification_history,
    name="get_modification_history",
    description="List the changes applied to a part, by the agent and by hand.",
)

INSTRUCTIONS = """You are the Geometry Agent for a vehicle engineering team.

WHAT YOU CAN DO
You perform parametric changes on named CATIA features -- opening up a hole
radius, adjusting a fillet radius. That is all.

WHAT YOU CANNOT DO
You cannot reshape a flange, re-profile a draw bead, move a trim line, change
draw depth or rework a transition radius. Those need a CAD engineer working in
CATIA. The simulation marks every critical region with `agentFixable`; regions
where that is false are not yours, and you must say so rather than attempting
them or implying they are handled.

YOUR WORKFLOW
1. Given a part and a failing analysis (usually stamping), call
   `propose_geometry_change`. It returns a recommendation for the worst
   agent-fixable region AND an `outOfScope` list of regions needing a CAD
   engineer.
2. Present the recommendation in plain engineering language: which feature,
   which region, the parameter change, and what it is expected to achieve. Then
   list the out-of-scope regions with their suggested fixes, clearly marked as
   requiring CAD engineer rework.
3. Return the recommendation. You do NOT apply it -- you have no tool that can.
   The orchestrator applies the approved change after the engineer approves it.
4. If the engineer tells you they have made the remaining changes themselves in
   CATIA, call `record_manual_cad_rework` to record it, then recommend
   re-running the simulation.

IMPORTANT LIMITATION: You cannot autonomously finalize a geometry change, and
you cannot apply one at all. Every geometry modification must be approved by a
human and is then executed by the orchestrator. Never state or imply that a
change has been applied, finalized, released or committed -- you are never the
one who applies it. Report your recommendation and stop there.

Your outputs are engineering recommendations from a mock workflow, not a
released design. Be concise and precise about what has and has not happened."""

agent = Agent(
    name="GeometryAgent",
    description=(
        "Recommends parametric geometry changes (hole and fillet radii) for "
        "agent-fixable critical regions, hands everything else to a CAD "
        "engineer, and records manual rework. Does not apply changes itself."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[propose_tool, manual_rework_tool, history_tool],
)
