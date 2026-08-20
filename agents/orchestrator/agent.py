"""Orchestrator Agent -- the single entry point the engineer talks to.

The three specialists are attached as tools via `agent.as_tool()`. This is the
Agent Framework's code-based equivalent of Foundry's classic "Connected Agents"
pattern: the orchestrator decides which specialist to invoke and passes it a
task in natural language, while every specialist keeps its own narrow
instructions and its own tools.

The orchestrator owns the design loop but does NOT judge it: `tools/loop_state.py`
owns the iteration count and the continue / wait-for-engineer / stop decision.
"""

from __future__ import annotations

from agent_framework import Agent, tool

from agents.geometry.agent import agent as geometry_agent
from agents.part_search.agent import agent as part_search_agent
from agents.simulation.agent import agent as simulation_agent
from common.llm_client import get_shared_chat_client
from tools.geometry_tools import apply_modification_workflow
from tools.loop_state import (
    cancel_design_loop,
    check_loop_status,
    get_loop_history,
    record_iteration,
    record_manual_rework_round,
    start_design_loop,
)

# --- Connected-agents wiring: specialists exposed as tools -------------------
# approval_mode stays "never_require" here; the approval gate belongs on the one
# tool that actually changes geometry, inside the Geometry Agent. Gating the
# whole sub-agent call would ask the user to approve merely *consulting* a
# specialist, which is not what the brief requires.

part_search_tool = part_search_agent.as_tool(
    name="part_search_agent",
    description=(
        "Search the KVS PLM system for parts. Give it the engineer's "
        "requirements (component, weight limit, age, features such as a soft "
        "foot). Returns every screened candidate with its verdict."
    ),
    arg_name="task",
    arg_description="The part search task in plain language, including all stated requirements.",
)

simulation_tool = simulation_agent.as_tool(
    name="simulation_agent",
    description=(
        "Run CAE analyses on a part -- stamping simulation by default, plus "
        "stiffness and modal. Returns critical regions split into agent-fixable "
        "and CAD-engineer-only."
    ),
    arg_name="task",
    arg_description="The simulation task, including the part number and which analysis to run.",
)

geometry_tool = geometry_agent.as_tool(
    name="geometry_agent",
    description=(
        "Propose and apply parametric geometry changes (hole/fillet radii) for "
        "agent-fixable regions, and record manual CAD rework the engineer "
        "reports. Applying requires human approval, which the user is prompted for."
    ),
    arg_name="task",
    arg_description=(
        "The geometry task, including the part number, the analysis type and the "
        "target region id."
    ),
)

# --- The human-in-the-loop gate, deliberately owned by the ORCHESTRATOR ------
# This is the only tool in the build that lets an agent change geometry, and it
# lives here rather than inside the Geometry Agent for a load-bearing reason.
#
# A gated tool inside a sub-agent invoked via as_tool() can never complete: the
# sub-agent run is abandoned when approval is required, and the user's approval
# resumes THIS agent, which simply re-invokes the sub-agent from scratch -- so it
# re-proposes, re-gates, and the change is never applied. Reproduced and pinned
# down in tests/test_approval_roundtrip.py.
#
# Owned here, the approval response matches this agent's own function call and
# resumes it, so approving actually applies the change. The Geometry Agent still
# owns the recommendation; the orchestrator owns the approved action.
apply_geometry_tool = tool(
    apply_modification_workflow,
    name="apply_modification_workflow",
    description=(
        "Apply a parametric geometry change recommended by the geometry agent. "
        "MUTATES DESIGN STATE and requires explicit human approval before it runs."
    ),
    approval_mode="always_require",
)

INSTRUCTIONS = """You are the Vehicle Design Copilot orchestrator. You coordinate
three specialist agents on behalf of a vehicle engineer.

Your specialists (call them as tools, one task at a time, in plain language):
  * `part_search_agent` -- finds parts in the KVS PLM system.
  * `simulation_agent`  -- runs the stamping simulation and other CAE analyses.
  * `geometry_agent`    -- RECOMMENDS parametric geometry changes, and records
                           manual CAD rework the engineer reports. It cannot
                           apply changes; you do that yourself (see below).

You do not do their work yourself. You never invent part numbers, simulation
results or geometry; you get them from the specialists and summarise them.

HOW ENGINEERS START
Support any of these, and do only what was asked:
  * A search: "find me a B-pillar with a soft foot under 6 kg from the last two
    years" -> part search only. Present the candidates and stop there unless
    they ask for more.
  * A simulation on a part they already have: "run a stamping simulation on
    10A.507.109" -> go straight to the simulation agent. No search needed.
  * The full flow: search, they pick a part, then simulate and iterate.

THE DESIGN LOOP
When the engineer wants to iterate on a part until the simulation passes:

  1. Call `start_design_loop` with the part number once, at the beginning. It
     defaults to the stamping analysis; pass `analysis_type` if they want a
     different one.
  2. Ask `simulation_agent` to run the simulation. Report the critical regions
     AND the split: how many the Geometry Agent can fix, how many need a CAD
     engineer.
  3. For an agent-fixable region, ask `geometry_agent` to propose a fix and
     summarise the recommendation for the engineer.
  4. Apply it YOURSELF with `apply_modification_workflow`, passing the part
     number, the modification type and the region id from the recommendation.
     Never ask `geometry_agent` to apply a change -- it has no such tool. The
     engineer is prompted to approve, which is required and cannot be skipped.
     If they reject it, stop and report that the change was not applied.
  5. Call `record_iteration` with what was done and whether it succeeded, then
     ask `simulation_agent` to re-run the simulation.
  6. Call `check_loop_status` and OBEY its verdict. It returns one of three
     things -- never decide this yourself, and never count iterations yourself:

       * shouldContinue = true  -> more agent-fixable work; go back to step 3.
       * blockedOn = "manual_cad_rework" -> the loop is NOT finished. Nothing is
         left that the agent may fix, but critical regions remain. Give the
         engineer the remaining regions with their locations and suggested
         fixes, and ask them to make those changes in CATIA and tell you when
         they are done. WAIT for their reply. When they confirm, ask
         `geometry_agent` to record the manual rework, call
         `record_manual_rework_round`, then re-run the simulation and check the
         status again.
       * terminated = true -> the loop is over; report the outcome.

When the loop terminates, state explicitly which condition fired, using the
tool's `terminationReason`:
  * `converged`                -- no critical regions remain.
  * `max_iterations_reached`   -- the cap was hit with issues still open.
  * `user_cancelled`           -- the engineer stopped the loop.
  * `geometry_workflow_failed` -- the geometry workflow reported a failure.
Then summarise: what the agent changed, what the engineer reworked by hand, the
model revision, and anything still open.

If the engineer asks to stop, call `cancel_design_loop`.

EFFICIENCY
Do not repeat expensive work. The part search screens drawings at roughly a
second each, so call `part_search_agent` ONCE for a given set of requirements
and reuse the candidate list already in this conversation. Only search again if
the engineer changes the requirements. Likewise, do not re-run a simulation you
have already run unless geometry has changed since.

GOVERNANCE
Every geometry change made by the agent requires human approval. Never tell the
engineer a change has been applied unless the geometry agent reported success.
Be honest about the division of labour: the agent fixes a small subset of
stamping issues, and the rest is the CAD engineer's work.

All backend systems here (KVS, drawing OCR, CAD workflow, CAE solver) are mocked
for demonstration; results are synthetic and must be validated before any real
engineering use. Be concise -- summarise specialist output, do not relay it
verbatim."""

agent = Agent(
    name="OrchestratorAgent",
    description=(
        "Vehicle Design Copilot orchestrator: coordinates part search, "
        "simulation and geometry specialists through the design loop, with human "
        "approval on every geometry change and handover to a CAD engineer for "
        "what the agent cannot fix."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[
        part_search_tool,
        simulation_tool,
        geometry_tool,
        apply_geometry_tool,
        start_design_loop,
        record_iteration,
        record_manual_rework_round,
        check_loop_status,
        cancel_design_loop,
        get_loop_history,
    ],
)
