"""Geometry Agent -- recommends geometry modifications and, once a human has
approved them, applies them through the (mock) parametric CAD workflow.

THE HUMAN-IN-THE-LOOP GATE LIVES HERE. `apply_modification_workflow` is wrapped
with approval_mode="always_require", so the Agent Framework suspends the run
and DevUI renders an Approve/Reject prompt before the tool ever executes. No
extra UI code is needed, and there is no code path that applies geometry
without that approval.
"""

from __future__ import annotations

from agent_framework import Agent, tool

from common.llm_client import get_shared_chat_client
from tools.geometry_tools import (
    apply_modification_workflow,
    get_modification_history,
    propose_geometry_change,
)

# Recommending is not changing -- no approval gate on the proposal tool.
propose_tool = tool(
    propose_geometry_change,
    name="propose_geometry_change",
    description=(
        "Recommend a geometry modification to resolve a critical region. "
        "Read-only: produces a recommendation only, changes nothing."
    ),
)

# The one tool in this demo that mutates design state, and the only one gated.
apply_tool = tool(
    apply_modification_workflow,
    name="apply_modification_workflow",
    description=(
        "Apply a geometry modification through the parametric CAD workflow. "
        "MUTATES DESIGN STATE and requires explicit human approval before it runs."
    ),
    approval_mode="always_require",
)

history_tool = tool(
    get_modification_history,
    name="get_modification_history",
    description="List the geometry modifications already applied to a part.",
)

INSTRUCTIONS = """You are the Geometry Agent for a vehicle engineering team.

You recommend and, once approved, apply parametric geometry modifications that
resolve critical regions found by simulation.

Your workflow:

1. Given a part and a failing analysis (stiffness, modal or stampability),
   call `propose_geometry_change` to produce a concrete recommendation:
   modification type, target region, parameters, and the expected effect.
2. Present that recommendation to the user in plain engineering language,
   including which region it targets and what it is expected to achieve.
3. Only if the user wants to proceed, call `apply_modification_workflow`.
   That tool requires explicit human approval and will pause for it.
4. After it returns successfully, report the job id, the model revision and
   the remaining severity, and recommend re-running the relevant simulation to
   confirm the change had the intended effect.

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
        "Recommends parametric geometry modifications to resolve critical "
        "regions, and applies them through the CAD workflow only after "
        "explicit human approval."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[propose_tool, apply_tool, history_tool],
)
