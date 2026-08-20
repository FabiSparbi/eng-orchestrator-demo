"""Offline walk-through of the demo's mechanics -- no Azure, no LLM.

Runs the same tool sequence the orchestrator drives in DevUI, calling the tools
directly. Use it to verify the mocked engineering logic and the convergence of
the CAD/CAE loop before (or without) standing up a Foundry deployment.

    python offline_demo.py

What this does NOT exercise: the agents themselves, the model's tool choices,
and DevUI's Approve/Reject panel. Those need real credentials and DevUI --
here the approval step is simply printed, and the approval gate itself is
covered by tests/test_approval_gate.py.
"""

from __future__ import annotations

from mock_data import digital_twin as twin
from tools import loop_state
from tools.geometry_tools import apply_modification_workflow, propose_geometry_change
from tools.part_search_tools import rank_candidates
from tools.simulation_tools import get_analysis_overview, run_stampability_analysis

REFERENCE_PART = "BR-2201"
ANALYSIS = "stampability"


def rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> None:
    twin.reset_all()
    loop_state.reset_all_sessions()

    # --- Step 1: coarse search (the MCP server's logic) --------------------
    rule("STEP 1  Part search -- coarse ePLM attribute filter (via MCP in DevUI)")
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent / "mcp_servers"))
    from eplm_mcp_server import search_eplm_coarse  # noqa: PLC0415

    search_fn = getattr(search_eplm_coarse, "fn", search_eplm_coarse)
    coarse = search_fn(material="aluminium", max_weight_kg=2.85)
    print(f"Filters: aluminium, <= 2.85 kg (the weight of {REFERENCE_PART})")
    print(f"Coarse hits: {coarse['matchCount']}")
    for part in coarse["parts"]:
        print(f"  {part['partNumber']}  {part['weightKg']:>5} kg  {part['name']}")

    # --- Step 2: fine search + ranking -------------------------------------
    rule("STEP 2  Part search -- fine drawing analysis and ranking")
    ranked = rank_candidates(REFERENCE_PART, [p["partNumber"] for p in coarse["parts"]])
    for candidate in ranked["candidates"]:
        print(
            f"  #{candidate['rank']}  {candidate['partNumber']}  "
            f"score={candidate['matchScore']:<6} fit={candidate['mountingPatternMatch']:<10} "
            f"saves {candidate['weightSavingKg']} kg"
        )
    selected = ranked["candidates"][0]["partNumber"]
    print(f"\nEngineer selects: {selected}")

    # --- Step 3: first simulation ------------------------------------------
    rule(f"STEP 3  Simulation -- {ANALYSIS} check on {selected}")
    loop_state.start_design_loop(
        selected, max_iterations=5, goal=f"clear {ANALYSIS} criticals", analysis_type=ANALYSIS
    )
    result = run_stampability_analysis(selected)
    print(result["summary"])
    for area in result["criticalAreas"]:
        print(f"  {area['regionId']}  severity={area['severity']}  {area['location']}")

    # --- Step 4-6: the loop -------------------------------------------------
    rule("STEP 4  Design loop -- propose, approve, apply, re-simulate")
    while True:
        status = loop_state.check_loop_status(selected)
        if not status["shouldContinue"]:
            break

        proposal = propose_geometry_change(selected, ANALYSIS)["proposal"]
        if proposal is None:
            break
        print(
            f"\n[iteration {status['iteration'] + 1}] Geometry Agent recommends: "
            f"{proposal['modificationType']} on {proposal['targetRegion']}"
        )
        print(f"  rationale: {proposal['rationale']}")
        print(f"  severity {proposal['currentSeverity']} -> expected {proposal['expectedSeverityAfter']}")

        # In DevUI this is where the run pauses for Approve/Reject.
        print("  [APPROVAL GATE] in DevUI the run pauses here -- auto-approved offline")

        applied = apply_modification_workflow(
            selected, proposal["modificationType"], ANALYSIS, proposal["targetRegion"]
        )
        print(f"  applied: {applied['status']}  job={applied.get('jobId')}  rev={applied.get('modelRevision')}")

        loop_state.record_iteration(
            selected, f"{proposal['modificationType']} on {proposal['targetRegion']}", applied["status"]
        )

        resim = run_stampability_analysis(selected)
        print(f"  re-simulation: {resim['status']}  ({len(resim['criticalAreas'])} critical region(s) left)")

    # --- Step 7: termination ------------------------------------------------
    rule("STEP 5  Loop termination")
    final = loop_state.check_loop_status(selected)
    print(f"Termination condition: {final['terminationReason']}")
    print(final["message"])
    overview = get_analysis_overview(selected)
    print(f"\nOverall status for {selected}: {overview['overallStatus'].upper()}")
    for analysis, detail in overview["byAnalysis"].items():
        print(f"  {analysis:<14} {detail['status']:<5} criticals={detail['criticalAreas']}  max severity={detail['maxSeverity']}")
    print(f"\nModifications applied: {overview['modificationsApplied']}")
    print("\nAll results above are synthetic -- mocked ePLM, drawings, CAD workflow and solver.")


if __name__ == "__main__":
    main()
