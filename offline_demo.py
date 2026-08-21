"""Offline walk-through of the demo's mechanics -- no Azure, no LLM.

Runs the same tool sequence the orchestrator drives in DevUI, calling the tools
directly. Use it to verify the mocked engineering logic and the shape of the
loop before (or without) standing up a Foundry deployment.

    python offline_demo.py

What this does NOT exercise: the agents themselves, the model's tool choices,
and DevUI's Approve/Reject panel. Those need real credentials and DevUI -- here
the approval step and the engineer's reply are simply printed. The approval gate
itself is covered by tests/test_approval_gate.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mock_data import digital_twin as twin
from tools import loop_state
from tools.drawing_analysis import run_fine_search
from tools.geometry_tools import (
    apply_modification_workflow,
    propose_geometry_change,
    record_manual_cad_rework,
)
from tools.simulation_tools import (
    get_analysis_overview,
    list_engineer_rework_items,
    run_stamping_simulation,
)

ANALYSIS = "stamping"


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


async def main() -> None:
    twin.reset_all()
    loop_state.reset_all_sessions()

    # --- Step 1: coarse search over KVS (the MCP server's logic) -----------
    rule("STEP 1  Coarse search -- KVS attribute filter (over MCP in DevUI)")
    sys.path.insert(0, str(Path(__file__).resolve().parent / "mcp_servers"))
    from kvs_mcp_server import search_kvs_coarse  # noqa: PLC0415

    search_fn = getattr(search_kvs_coarse, "fn", search_kvs_coarse)
    coarse = search_fn(component="B-pillar", max_weight_kg=6.0, created_within_years=2)
    print("Engineer asks for: a B-pillar with a soft foot, max 6 kg, created in the last 2 years")
    print(f"KVS filter: component=B-pillar (507), <= 6.0 kg, created after {coarse['filtersApplied']['created_after']}")
    print(f"Catalog holds {coarse['catalogSize']} parts -> {coarse['matchCount']} pass the coarse filter\n")
    for part in coarse["parts"]:
        print(f"  {part['partNumber']:<14}{part['weightKg']:>5} kg   {part['createdDate']}   {part['materialGrade']}")
    print("\nNote: KVS knows nothing about a soft foot. That needs the drawings.")

    # --- Step 2: fine search -- drawing OCR + soft-foot rule ---------------
    rule("STEP 2  Fine search -- drawing OCR and the soft-foot rule (the slow step)")
    candidates = [p["partNumber"] for p in coarse["parts"]]
    fine = await run_fine_search(candidates)
    print(f"Rule: {fine['rule']}")
    print(f"Screened {fine['screenedCount']} drawings in ~{fine['approxSecondsSpent']}s\n")
    print(f"  {'part':<14}{'kg':>6}  {'created':<12}{'soft foot':<11}HV values found")
    for s in fine["screened"]:
        mark = "YES" if s["hasSoftFoot"] else "no"
        print(f"  {s['partNumber']:<14}{s['weightKg']:>6}  {s['createdDate']:<12}{mark:<11}{s['hvValues']}")

    selected = fine["partsWithSoftFoot"][0]
    print(f"\nEvidence for {selected}:")
    hero = next(s for s in fine["screened"] if s["partNumber"] == selected)
    for line in hero["evidence"]:
        print(f"    {line}")
    print(f"\nEngineer reviews the list and selects: {selected}")

    # --- Step 3: stamping simulation ---------------------------------------
    rule(f"STEP 3  Stamping simulation on {selected}")
    loop_state.start_design_loop(selected, goal="clear stamping criticals", analysis_type=ANALYSIS)
    result = run_stamping_simulation(selected)
    print(result["summary"] + "\n")
    for area in result["criticalAreas"]:
        tag = "AGENT" if area["agentFixable"] else "CAD ENG"
        print(f"  [{tag:<7}] {area['regionId']}  {area['regionType']:<7} sev={area['severity']}  {area['location']}")
        print(f"              -> {area['suggestedFix']}")

    # --- Step 4: the agent fixes what it legitimately can ------------------
    rule("STEP 4  Design loop -- the Geometry Agent fixes what it can")
    while True:
        status = loop_state.check_loop_status(selected)
        if not status["shouldContinue"]:
            break

        proposal = propose_geometry_change(selected, ANALYSIS)["proposal"]
        print(f"\n[iteration {status['iteration'] + 1}] Geometry Agent recommends:")
        print(f"  {proposal['modificationType']} on feature {proposal['featureId']} ({proposal['targetRegion']})")
        print(f"  {proposal['parameters']['fromMm']} mm -> {proposal['parameters']['toMm']} mm")
        print(f"  rationale: {proposal['rationale']}")
        print(f"  severity {proposal['currentSeverity']} -> expected {proposal['expectedSeverityAfter']}")
        print("  [APPROVAL GATE] in DevUI the run pauses here -- auto-approved offline")

        applied = apply_modification_workflow(
            selected, proposal["modificationType"], proposal["targetRegion"], ANALYSIS
        )
        print(f"  applied: {applied['status']}  job={applied.get('jobId')}  rev={applied.get('modelRevision')}")
        loop_state.record_iteration(
            selected, f"{proposal['modificationType']} on {proposal['targetRegion']}", applied["status"]
        )
        resim = run_stamping_simulation(selected)
        print(f"  re-simulation: {resim['criticalAreaCount']} critical region(s) left "
              f"({resim['agentFixableCount']} agent-fixable, {resim['engineerOnlyCount']} CAD engineer)")

    # --- Step 5: handover to the CAD engineer ------------------------------
    status = loop_state.check_loop_status(selected)
    rule("STEP 5  Handover -- human in the loop for what the agent cannot fix")
    print(status["message"] + "\n")
    worklist = list_engineer_rework_items(selected, ANALYSIS)
    print(f"Worklist for the CAD engineer ({worklist['itemCount']} items):")
    for item in worklist["worklist"]:
        print(f"  {item['regionId']}  {item['regionType']:<7} sev={item['severity']}  {item['location']}")
        print(f"            -> {item['suggestedFix']}")

    print("\n  [ENGINEER REPLIES IN CHAT] \"I've made those changes in CATIA -- changes applied.\"")
    record_manual_cad_rework(selected, ANALYSIS, note="Flange, wall, radius, bead and trim rework in CATIA")
    loop_state.record_manual_rework_round(selected, "CAD engineer reworked the remaining regions")

    # --- Step 6: re-simulate and finish ------------------------------------
    rule("STEP 6  Re-simulation and loop termination")
    final_sim = run_stamping_simulation(selected)
    print(final_sim["summary"] + "\n")
    final = loop_state.check_loop_status(selected)
    print(f"Termination condition: {final['terminationReason']}")
    print(final["message"])

    overview = get_analysis_overview(selected)
    scope_status = overview["byAnalysis"][ANALYSIS]["status"].upper()
    print(f"\n{ANALYSIS.capitalize()} (this loop's scope) for {selected}: {scope_status}")
    print("\nOther analyses -- not part of this loop, shown for context:")
    for analysis, detail in overview["byAnalysis"].items():
        if analysis == ANALYSIS:
            continue
        print(f"  {analysis:<11}{detail['status']:<6} criticals={detail['criticalAreas']}  max severity={detail['maxSeverity']}")
    print(f"\nAgent modifications: {overview['modificationsApplied']}   "
          f"Manual rework rounds: {overview['manualReworkRounds']}")
    print("\nAll results above are synthetic -- mocked KVS, drawings, OCR, CAD workflow and solver.")


if __name__ == "__main__":
    asyncio.run(main())
