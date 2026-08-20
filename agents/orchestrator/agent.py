"""Orchestrator Agent -- the single entry point the engineer talks to.

The three specialists are attached as tools via `agent.as_tool()`. This is the
Agent Framework's code-based equivalent of Foundry's classic "Connected Agents"
pattern: the orchestrator decides which specialist to invoke and passes it a
task in natural language, while every specialist keeps its own narrow
instructions and its own tools.

The orchestrator also owns the design loop, but it does NOT count iterations in
its head -- `tools/loop_state.py` owns the counter and the termination decision
deterministically, in code.
"""

from __future__ import annotations

from agent_framework import Agent

from agents.geometry.agent import agent as geometry_agent
from agents.part_search.agent import agent as part_search_agent
from agents.simulation.agent import agent as simulation_agent
from common.llm_client import get_shared_chat_client
from tools.loop_state import (
    cancel_design_loop,
    check_loop_status,
    get_loop_history,
    record_iteration,
    start_design_loop,
)

# --- Connected-agents wiring: specialists exposed as tools -------------------
# approval_mode stays "never_require" here; the approval gate belongs on the
# one tool that actually changes geometry, inside the Geometry Agent. Gating
# the whole sub-agent call would ask the user to approve merely *consulting*
# the specialist, which is not what the brief requires.

part_search_tool = part_search_agent.as_tool(
    name="part_search_agent",
    description=(
        "Search the ePLM system for replacement part candidates. Give it the "
        "reference part number and the requirement (e.g. lighter, same "
        "mounting points). Returns ranked candidates with match scores."
    ),
    arg_name="task",
    arg_description="The part search task, in plain language, including the reference part number.",
)

simulation_tool = simulation_agent.as_tool(
    name="simulation_agent",
    description=(
        "Run CAE analyses (stiffness, modal, stampability) on a part and get "
        "back pass/fail status plus any critical regions."
    ),
    arg_name="task",
    arg_description="The simulation task, including the part number and which analysis to run.",
)

geometry_tool = geometry_agent.as_tool(
    name="geometry_agent",
    description=(
        "Recommend a geometry modification for a failing region, and apply it "
        "through the CAD workflow. Applying requires human approval, which the "
        "user will be prompted for."
    ),
    arg_name="task",
    arg_description=(
        "The geometry task, including the part number, the failing analysis type "
        "and the target region id."
    ),
)

INSTRUCTIONS = """You are the Vehicle Design Copilot orchestrator. You coordinate
three specialist agents on behalf of a vehicle engineer.

Your specialists (call them as tools, one task at a time, in plain language):
  * `part_search_agent` -- finds and ranks replacement part candidates in ePLM.
  * `simulation_agent`  -- runs stiffness, modal and stampability analyses.
  * `geometry_agent`    -- recommends geometry changes and applies approved ones.

You do not do their work yourself. You never invent part numbers, simulation
results or geometry; you get them from the specialists and summarise them.

THE DESIGN LOOP
When the engineer wants to iterate on a part until it passes:

  1. Call `start_design_loop` with the part number once, at the beginning.
  2. Ask `simulation_agent` to analyse the part.
  3. If critical regions remain, ask `geometry_agent` to propose a fix for the
     worst region, and summarise that recommendation for the engineer.
  4. Ask `geometry_agent` to apply it. The engineer will be prompted to approve
     the change -- this is required and cannot be skipped. If they reject it,
     stop and report that the change was not applied.
  5. After the change is applied, call `record_iteration` with the action and
     whether it succeeded, then ask `simulation_agent` to re-run the analysis.
  6. Call `check_loop_status` and obey its `shouldContinue` verdict. Do NOT
     decide for yourself whether the loop should continue and do NOT count
     iterations yourself -- that tool owns the count and the decision.
  7. Repeat from step 3 while `shouldContinue` is true.

When the loop ends, state explicitly which termination condition fired, using
the tool's `terminationReason`:
  * `converged`                -- no critical regions remain.
  * `max_iterations_reached`   -- the iteration cap was hit with issues open.
  * `user_cancelled`           -- the engineer stopped the loop.
  * `geometry_workflow_failed` -- the geometry workflow reported a failure.
Then summarise what changed: modifications applied, revision, remaining issues.

If the engineer asks to stop, call `cancel_design_loop`.

GOVERNANCE
Every geometry change requires human approval. Never tell the engineer a change
has been applied unless the geometry agent actually reported success.

All backend systems here (ePLM, drawing analysis, CAD workflow, CAE solver) are
mocked for demonstration; results are synthetic and must be validated before any
real engineering use. Be concise -- summarise specialist output, do not just
relay it verbatim."""

agent = Agent(
    name="OrchestratorAgent",
    description=(
        "Vehicle Design Copilot orchestrator: coordinates part search, "
        "simulation and geometry specialists through the design-iteration loop "
        "with human approval on every geometry change."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[
        part_search_tool,
        simulation_tool,
        geometry_tool,
        start_design_loop,
        record_iteration,
        check_loop_status,
        cancel_design_loop,
        get_loop_history,
    ],
)
