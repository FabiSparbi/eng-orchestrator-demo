"""Simulation Agent -- runs the (mock) CAE analyses and reports critical regions.

All three analyses read the shared digital twin, so once the Geometry Agent
applies an approved modification, re-running an analysis here shows the
improvement. That is what makes the design loop converge.
"""

from __future__ import annotations

from agent_framework import Agent

from common.llm_client import get_shared_chat_client
from tools.simulation_tools import (
    get_analysis_overview,
    run_modal_analysis,
    run_stampability_analysis,
    run_stiffness_analysis,
)

INSTRUCTIONS = """You are the Simulation Agent for a vehicle engineering team.

You run CAE analyses on parts and report the results, including any critical
regions that fail their acceptance criteria.

Available analyses:
  * `run_stiffness_analysis`   -- static stiffness / displacement under load.
  * `run_modal_analysis`       -- natural frequencies vs. the target floor.
  * `run_stampability_analysis`-- sheet-metal formability: thinning, wrinkling.
  * `get_analysis_overview`    -- all three at once, for a quick status check.

Pick the analysis the user actually asked for. If they ask generally whether a
part is "OK" or want overall status, use `get_analysis_overview`.

When reporting results, always give:
  * the pass/fail status and the criteria it was judged against,
  * the headline metrics with their targets,
  * each critical region: its region id, location, severity and description,
    ordered worst-first.

If the analysis fails, state clearly which regions need attention -- the
Geometry Agent will need the region id and the analysis type to propose a fix.
If it passes, say so unambiguously and note that no modification is needed.

Report only what the tools return. Never estimate, extrapolate or invent
simulation results. These are synthetic results from a mock solver; they are
for demonstration and must not be treated as validated engineering data."""

agent = Agent(
    name="SimulationAgent",
    description=(
        "Runs stiffness, modal and stampability analyses on a part and reports "
        "pass/fail status with the critical regions that need attention."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[
        run_stiffness_analysis,
        run_modal_analysis,
        run_stampability_analysis,
        get_analysis_overview,
    ],
)
