"""Geometry Agent -- parametric changes it can make, and honest handover of
everything it cannot.

THE HUMAN-IN-THE-LOOP GATE LIVES HERE. `apply_modification_workflow` is wrapped
with approval_mode="always_require", so the Agent Framework suspends the run and
DevUI renders an Approve/Reject prompt before the tool ever executes. There is
no code path that applies geometry without that approval.

`record_manual_cad_rework` also changes state but is deliberately NOT gated: it
records what a CAD engineer says they already did by hand in CATIA. The gate
exists to stop an agent changing geometry unsupervised; asking the engineer to
approve their own report would be noise.
"""

from __future__ import annotations

from agent_framework import Agent, tool

from common.llm_client import get_shared_chat_client
from tools.geometry_tools import (
    apply_modification_workflow,
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

# The one tool where the AGENT mutates design state, and the only one gated.
apply_tool = tool(
    apply_modification_workflow,
    name="apply_modification_workflow",
    description=(
        "Apply a parametric geometry change through the CAD workflow. MUTATES "
        "DESIGN STATE and requires explicit human approval before it runs."
    ),
    approval_mode="always_require",
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
3. Only if the engineer wants to proceed, call `apply_modification_workflow`.
   That tool requires explicit human approval and will pause for it.
4. After it returns successfully, report the job id, the model revision, the
   region's new severity, and how many regions remain in each category. Then
   recommend re-running the simulation to confirm the effect.
5. If the engineer tells you they have made the remaining changes themselves in
   CATIA, call `record_manual_cad_rework` to record it, then recommend
   re-running the simulation.

IMPORTANT LIMITATION: You cannot autonomously finalize a geometry change.
Every geometry modification must be approved by a human before it is applied.
Never state or imply that a change has been applied, finalized, released or
committed unless `apply_modification_workflow` has actually returned a success
result. If approval is refused, or the tool returns a failure, say so plainly
and do not retry the same modification without new instructions.

Your outputs are engineering recommendations from a mock workflow, not a
released design. Be concise and precise about what has and has not happened."""

agent = Agent(
    name="GeometryAgent",
    description=(
        "Applies parametric geometry changes (hole and fillet radii) to "
        "agent-fixable critical regions after explicit human approval, and hands "
        "everything else to a CAD engineer."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[propose_tool, apply_tool, manual_rework_tool, history_tool],
)
