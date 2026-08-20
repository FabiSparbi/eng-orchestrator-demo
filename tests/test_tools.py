"""Unit tests for the mocked tool layer.

No LLM and no Azure credentials are involved -- these call the tool functions
directly, so the mocked engineering logic (and above all the convergence
behaviour of the CAD/CAE loop) is proven before any agent is wired up.

Run:  .venv/bin/python -m pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mock_data import digital_twin as twin
from tools import loop_state
from tools.drawing_analysis import analyze_drawing, compare_mounting_interfaces
from tools.geometry_tools import (
    apply_modification_workflow,
    get_modification_history,
    propose_geometry_change,
)
from tools.part_search_tools import rank_candidates
from tools.simulation_tools import (
    get_analysis_overview,
    run_modal_analysis,
    run_stampability_analysis,
    run_stiffness_analysis,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts from the pristine baseline twin and no loop sessions."""
    twin.reset_all()
    loop_state.reset_all_sessions()
    yield
    twin.reset_all()
    loop_state.reset_all_sessions()


# --------------------------------------------------------------------------
# Coarse search (the MCP server's logic, exercised in-process)
# --------------------------------------------------------------------------

def _catalog():
    with (REPO_ROOT / "mock_data" / "eplm_catalog.json").open(encoding="utf-8") as fh:
        return json.load(fh)["parts"]


def test_catalog_is_wellformed():
    parts = _catalog()
    assert len(parts) >= 8
    required = {"partNumber", "material", "weightKg", "publishedDate", "drawingRef", "status"}
    for part in parts:
        assert required <= set(part), f"{part.get('partNumber')} missing fields"


def test_mcp_server_module_imports_and_filters():
    """The MCP server's search function filters on attributes only."""
    sys.path.insert(0, str(REPO_ROOT / "mcp_servers"))
    from eplm_mcp_server import search_eplm_coarse  # noqa: PLC0415

    fn = getattr(search_eplm_coarse, "fn", search_eplm_coarse)

    result = fn(material="aluminium", max_weight_kg=2.0)
    assert result["matchCount"] > 0
    for part in result["parts"]:
        assert part["material"] == "aluminium"
        assert part["weightKg"] <= 2.0

    # Date filter excludes older parts.
    recent = fn(published_after="2024-01-01")
    assert all(p["publishedDate"] >= "2024-01-01" for p in recent["parts"])

    # Results are weight-sorted (lightest first) -- useful for "find lighter".
    weights = [p["weightKg"] for p in result["parts"]]
    assert weights == sorted(weights)


def test_coarse_search_returns_nothing_for_impossible_filter():
    sys.path.insert(0, str(REPO_ROOT / "mcp_servers"))
    from eplm_mcp_server import search_eplm_coarse  # noqa: PLC0415

    fn = getattr(search_eplm_coarse, "fn", search_eplm_coarse)
    assert fn(material="unobtainium")["matchCount"] == 0


@pytest.mark.asyncio
async def test_mcp_server_speaks_mcp_over_stdio():
    """The ePLM server is a real MCP server, not just an importable module.

    Uses the MCP client library so the handshake is driven by the protocol
    itself -- piping raw JSON-RPC and reading after exit races the server's
    reply against stdin EOF, which made an earlier version of this test flaky.
    """
    from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
    from mcp.client.stdio import stdio_client  # noqa: PLC0415

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "mcp_servers" / "eplm_mcp_server.py")],
        cwd=str(REPO_ROOT),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            assert "search_eplm_coarse" in names
            assert "get_part_record" in names

            called = await session.call_tool(
                "search_eplm_coarse", {"material": "aluminium", "max_weight_kg": 2.0}
            )
            payload = json.loads(called.content[0].text)
            assert payload["matchCount"] > 0
            for part in payload["parts"]:
                assert part["material"] == "aluminium"
                assert part["weightKg"] <= 2.0


# --------------------------------------------------------------------------
# Fine search / drawing analysis
# --------------------------------------------------------------------------

def test_analyze_drawing_returns_required_fields():
    result = analyze_drawing("BR-2201")
    for field in ("hole_count", "mounting_pattern", "distinguishing_features", "confidence"):
        assert field in result
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["hole_count"] == 4


def test_analyze_drawing_unknown_part_is_honest():
    result = analyze_drawing("ZZ-9999")
    assert result["confidence"] == 0.0
    assert result["hole_count"] is None


def test_identical_mounting_pattern_scores_as_exact():
    """BR-3310 is the intended drop-in replacement for BR-2201."""
    result = compare_mounting_interfaces("BR-2201", "BR-3310")
    assert result["mountingPatternMatch"] == "exact"
    assert result["geometricMatchScore"] >= 0.85


def test_different_bolt_pattern_is_rejected_despite_low_weight():
    """The point of fine search: BR-4120 is lighter but does not fit."""
    result = compare_mounting_interfaces("BR-2201", "BR-4120")
    assert result["mountingPatternMatch"] == "different"
    assert any("Hole count differs" in d for d in result["differences"])


# --------------------------------------------------------------------------
# Candidate ranking
# --------------------------------------------------------------------------

def test_rank_candidates_orders_fit_over_raw_weight():
    result = rank_candidates("BR-2201", ["BR-4120", "BR-3310", "BR-5501"])
    assert result["candidateCount"] == 3
    ranks = [c["partNumber"] for c in result["candidates"]]
    assert ranks[0] == "BR-3310", ranks
    # The lighter-but-wrong-pattern part must not win.
    assert ranks.index("BR-4120") == len(ranks) - 1


def test_ranked_candidate_schema_fields_present():
    candidate = rank_candidates("BR-2201", ["BR-3310"])["candidates"][0]
    for field in (
        "partNumber",
        "rank",
        "matchScore",
        "weightKg",
        "weightSavingKg",
        "mountingPatternMatch",
        "distinguishingFeatures",
        "confidence",
        "rationale",
    ):
        assert field in candidate, f"missing {field}"
    assert candidate["weightSavingKg"] > 0


def test_rank_candidates_skips_unknown_part_numbers():
    result = rank_candidates("BR-2201", ["BR-3310", "NOT-A-PART"])
    assert result["candidateCount"] == 1


# --------------------------------------------------------------------------
# Simulation over the digital twin
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fn,analysis",
    [
        (run_stiffness_analysis, "stiffness"),
        (run_modal_analysis, "modal"),
        (run_stampability_analysis, "stampability"),
    ],
)
def test_analysis_returns_unified_schema(fn, analysis):
    result = fn("BR-3310")
    assert result["analysisType"] == analysis
    assert result["status"] in ("pass", "fail")
    assert isinstance(result["criticalAreas"], list)
    assert "metrics" in result and "summary" in result
    for area in result["criticalAreas"]:
        assert {"regionId", "location", "severity", "description"} <= set(area)


def test_baseline_part_fails_before_any_modification():
    result = run_stampability_analysis("BR-3310")
    assert result["status"] == "fail"
    assert len(result["criticalAreas"]) == 2


def test_unknown_part_gets_generic_profile_not_a_crash():
    result = run_stiffness_analysis("BR-9999")
    assert result["status"] in ("pass", "fail")
    assert result["partId"] == "BR-9999"


def test_severity_threshold_governs_critical_area_reporting():
    state = twin.get_part_state("BR-3310")
    state["analyses"]["stampability"]["areas"][0]["severity"] = twin.SEVERITY_THRESHOLD - 0.01
    state["analyses"]["stampability"]["areas"][1]["severity"] = twin.SEVERITY_THRESHOLD - 0.01
    assert run_stampability_analysis("BR-3310")["status"] == "pass"


# --------------------------------------------------------------------------
# Geometry tools
# --------------------------------------------------------------------------

def test_propose_does_not_change_anything():
    before = run_stampability_analysis("BR-3310")["maxSeverity"]
    proposal = propose_geometry_change("BR-3310", "stampability")
    assert proposal["applied"] is False
    assert proposal["proposal"]["modificationType"] == "increase_fillet_radius"
    after = run_stampability_analysis("BR-3310")["maxSeverity"]
    assert before == after, "propose_geometry_change must be read-only"


def test_propose_targets_worst_region_by_default():
    proposal = propose_geometry_change("BR-3310", "stampability")
    assert proposal["proposal"]["targetRegion"] == "R-STP-01"  # severity 0.71 vs 0.44


def test_apply_modification_reduces_severity():
    before = twin.max_severity("BR-3310", "stampability")
    result = apply_modification_workflow("BR-3310", "increase_fillet_radius", "stampability")
    assert result["status"] == "success"
    assert result["jobId"].startswith("GEOM-BR-3310")
    assert twin.max_severity("BR-3310", "stampability") < before


def test_apply_modification_rejects_unknown_modification_type():
    result = apply_modification_workflow("BR-3310", "frobnicate_the_widget", "stampability")
    assert result["status"] == "failure"
    assert "not supported" in result["reason"]


def test_apply_modification_rejects_unknown_analysis_type():
    result = apply_modification_workflow("BR-3310", "increase_fillet_radius", "telepathy")
    assert result["status"] == "failure"


def test_apply_modification_fails_when_nothing_left_to_fix():
    for _ in range(4):
        apply_modification_workflow("BR-3310", "increase_fillet_radius", "stampability")
    result = apply_modification_workflow("BR-3310", "increase_fillet_radius", "stampability")
    assert result["status"] == "failure"
    assert "No open critical region" in result["reason"]


def test_modification_history_tracks_revisions():
    apply_modification_workflow("BR-3310", "increase_fillet_radius", "stampability")
    apply_modification_workflow("BR-3310", "increase_rib_thickness", "stiffness")
    history = get_modification_history("BR-3310")
    assert history["modificationCount"] == 2
    assert history["currentRevision"] == "rev-02"


# --------------------------------------------------------------------------
# THE key behaviour: the loop converges
# --------------------------------------------------------------------------

def test_repeated_modifications_converge_to_pass():
    """Simulate -> modify -> re-simulate must reach 'pass', not loop forever."""
    assert run_stampability_analysis("BR-3310")["status"] == "fail"

    iterations = 0
    while run_stampability_analysis("BR-3310")["status"] == "fail" and iterations < 5:
        proposal = propose_geometry_change("BR-3310", "stampability")["proposal"]
        result = apply_modification_workflow(
            "BR-3310", proposal["modificationType"], "stampability", proposal["targetRegion"]
        )
        assert result["status"] == "success"
        iterations += 1

    assert run_stampability_analysis("BR-3310")["status"] == "pass"
    assert iterations <= 3, f"took {iterations} iterations -- too slow for a demo"
    assert run_stampability_analysis("BR-3310")["criticalAreas"] == []


def test_modifications_have_coupled_side_effects():
    """A stampability fix also helps stiffness a little, as a real change would."""
    before = twin.max_severity("BR-3310", "stiffness")
    apply_modification_workflow("BR-3310", "increase_fillet_radius", "stampability")
    assert twin.max_severity("BR-3310", "stiffness") < before


def test_full_part_reaches_overall_pass():
    for analysis in ("stiffness", "modal", "stampability"):
        for _ in range(3):
            if not twin.critical_areas("BR-3310", analysis):
                break
            proposal = propose_geometry_change("BR-3310", analysis)["proposal"]
            apply_modification_workflow("BR-3310", proposal["modificationType"], analysis)
    assert get_analysis_overview("BR-3310")["overallStatus"] == "pass"


# --------------------------------------------------------------------------
# Loop termination -- all four conditions from the brief
# --------------------------------------------------------------------------

def test_loop_starts_and_reports_continue():
    loop_state.start_design_loop("BR-3310")
    status = loop_state.check_loop_status("BR-3310")
    assert status["shouldContinue"] is True
    assert status["iteration"] == 0


def test_iteration_counter_is_deterministic_not_model_memory():
    loop_state.start_design_loop("BR-3310")
    for i in range(3):
        result = loop_state.record_iteration("BR-3310", f"mod {i}", "success")
        assert result["iteration"] == i + 1


def test_termination_condition_converged():
    loop_state.start_design_loop("BR-3310")
    for analysis in ("stiffness", "modal", "stampability"):
        for _ in range(3):
            if not twin.critical_areas("BR-3310", analysis):
                break
            proposal = propose_geometry_change("BR-3310", analysis)["proposal"]
            apply_modification_workflow("BR-3310", proposal["modificationType"], analysis)
    status = loop_state.check_loop_status("BR-3310")
    assert status["shouldContinue"] is False
    assert status["terminationReason"] == "converged"
    assert "no critical regions remain" in status["message"]


def test_termination_condition_max_iterations():
    loop_state.start_design_loop("BR-7150", max_iterations=2)
    loop_state.record_iteration("BR-7150", "mod 1", "success")
    loop_state.record_iteration("BR-7150", "mod 2", "success")
    status = loop_state.check_loop_status("BR-7150")
    assert status["shouldContinue"] is False
    assert status["terminationReason"] == "max_iterations_reached"


def test_termination_condition_user_cancelled():
    loop_state.start_design_loop("BR-3310")
    loop_state.cancel_design_loop("BR-3310", reason="switching to a different concept")
    status = loop_state.check_loop_status("BR-3310")
    assert status["shouldContinue"] is False
    assert status["terminationReason"] == "user_cancelled"


def test_termination_condition_geometry_workflow_failed():
    loop_state.start_design_loop("BR-3310")
    loop_state.record_iteration("BR-3310", "bad modification", "failure")
    status = loop_state.check_loop_status("BR-3310")
    assert status["shouldContinue"] is False
    assert status["terminationReason"] == "geometry_workflow_failed"


def test_check_status_without_a_loop_is_safe():
    status = loop_state.check_loop_status("BR-0000")
    assert status["shouldContinue"] is False
    assert status["terminationReason"] == "no_active_loop"


def test_loop_history_is_recorded():
    loop_state.start_design_loop("BR-3310", goal="reduce mass")
    loop_state.record_iteration("BR-3310", "increase_fillet_radius", "success", "stampability now passes")
    history = loop_state.get_loop_history("BR-3310")
    assert history["goal"] == "reduce mass"
    assert history["iterationsCompleted"] == 1
    assert history["history"][0]["action"] == "increase_fillet_radius"


# --------------------------------------------------------------------------
# Loop scoping (regression: a single-analysis loop must be able to converge)
# --------------------------------------------------------------------------

def test_scoped_loop_converges_when_its_own_analysis_passes():
    """A loop scoped to stampability converges even if stiffness is still open."""
    loop_state.start_design_loop("BR-3310", analysis_type="stampability")
    for _ in range(4):
        if not twin.critical_areas("BR-3310", "stampability"):
            break
        proposal = propose_geometry_change("BR-3310", "stampability")["proposal"]
        apply_modification_workflow(
            "BR-3310", proposal["modificationType"], "stampability", proposal["targetRegion"]
        )
    status = loop_state.check_loop_status("BR-3310")
    assert status["analysisScope"] == "stampability"
    assert status["criticalAreasRemaining"] == 0
    assert status["terminationReason"] == "converged"


def test_unscoped_loop_still_requires_every_analysis():
    """Without a scope, one passing analysis must not end the loop."""
    loop_state.start_design_loop("BR-3310")
    for _ in range(4):
        if not twin.critical_areas("BR-3310", "stampability"):
            break
        proposal = propose_geometry_change("BR-3310", "stampability")["proposal"]
        apply_modification_workflow(
            "BR-3310", proposal["modificationType"], "stampability", proposal["targetRegion"]
        )
    assert run_stampability_analysis("BR-3310")["status"] == "pass"
    status = loop_state.check_loop_status("BR-3310")
    assert status["analysisScope"] == "all"
    # Other analyses may or may not have cleared via coupling; either way the
    # scope reported must cover all three, not just stampability.
    assert status["criticalAreasRemaining"] == status["criticalAreasAllAnalyses"]


def test_start_design_loop_rejects_unknown_scope():
    result = loop_state.start_design_loop("BR-3310", analysis_type="telepathy")
    assert "error" in result


def test_targeted_modification_still_couples_into_other_analyses():
    """Regression: naming a region must not cancel the cross-analysis coupling.

    Region ids are per-analysis, so filtering every analysis by the target
    region id used to leave the other analyses completely untouched.
    """
    stiffness_before = twin.max_severity("BR-3310", "stiffness")
    result = apply_modification_workflow(
        "BR-3310", "increase_fillet_radius", "stampability", "R-STP-01"
    )
    assert result["status"] == "success"
    assert twin.max_severity("BR-3310", "stiffness") < stiffness_before


def test_targeted_modification_only_hits_its_own_region_within_its_analysis():
    """The region filter still scopes precisely inside the targeted analysis."""
    other_before = next(
        a["severity"] for a in twin.critical_areas("BR-3310", "stampability") if a["regionId"] == "R-STP-02"
    )
    apply_modification_workflow("BR-3310", "increase_fillet_radius", "stampability", "R-STP-01")
    other_after = next(
        a["severity"] for a in twin.critical_areas("BR-3310", "stampability") if a["regionId"] == "R-STP-02"
    )
    assert other_after == other_before
