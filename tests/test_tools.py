"""Unit tests for the mocked tool layer.

No LLM and no Azure credentials are involved -- these call the tool functions
directly, so the mocked engineering logic is proven before any agent is wired up.

Run:  .venv/bin/python -m pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mock_data import digital_twin as twin
from mock_data.drawing_ocr import SOFT_FOOT_PARTS, get_drawing_ocr
from tools import loop_state
from tools.drawing_analysis import (
    analyze_drawing,
    evaluate_soft_foot,
    extract_hv_values,
    run_fine_search,
)
from tools.geometry_tools import (
    apply_modification_workflow,
    get_modification_history,
    propose_geometry_change,
    record_manual_cad_rework,
)
from tools.simulation_tools import (
    get_analysis_overview,
    list_engineer_rework_items,
    run_modal_analysis,
    run_stamping_simulation,
    run_stiffness_analysis,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HERO = "10A.507.109"          # the one part with a soft foot
B_PILLAR_CODE = "507"


@pytest.fixture(autouse=True)
def clean_state():
    twin.reset_all()
    loop_state.reset_all_sessions()
    yield
    twin.reset_all()
    loop_state.reset_all_sessions()


def _coarse_search():
    sys.path.insert(0, str(REPO_ROOT / "mcp_servers"))
    from kvs_mcp_server import search_kvs_coarse  # noqa: PLC0415

    return getattr(search_kvs_coarse, "fn", search_kvs_coarse)


def _catalog():
    with (REPO_ROOT / "mock_data" / "kvs_catalog.json").open(encoding="utf-8") as fh:
        return json.load(fh)["parts"]


# --------------------------------------------------------------------------
# KVS catalog
# --------------------------------------------------------------------------

def test_catalog_is_wellformed():
    parts = _catalog()
    assert len(parts) == 50
    required = {"partNumber", "component", "vehicleModel", "weightKg", "createdDate", "drawingRef", "status"}
    for part in parts:
        assert required <= set(part), f"{part.get('partNumber')} missing fields"


def test_catalog_holds_forty_bpillars_and_some_decoys():
    parts = _catalog()
    bpillars = [p for p in parts if p["partNumber"].split(".")[1] == B_PILLAR_CODE]
    assert len(bpillars) == 40
    assert len(parts) - len(bpillars) == 10, "decoy components are what make the component filter meaningful"


def test_part_numbers_follow_the_model_component_id_format():
    for part in _catalog():
        segments = part["partNumber"].split(".")
        assert len(segments) == 3, part["partNumber"]
        assert segments[1].isdigit() and len(segments[1]) == 3


def test_catalog_leaks_no_drawing_derived_features():
    """A soft foot must only be discoverable by processing the drawing."""
    raw = (REPO_ROOT / "mock_data" / "kvs_catalog.json").read_text(encoding="utf-8").lower()
    assert "softfoot" not in raw and "soft foot" not in raw
    assert "hv" not in {k.lower() for part in _catalog() for k in part}


# --------------------------------------------------------------------------
# Coarse search (the KVS MCP server's logic)
# --------------------------------------------------------------------------

def test_coarse_search_filters_to_bpillars_only():
    result = _coarse_search()(component="B-pillar")
    assert result["matchCount"] == 40
    for part in result["parts"]:
        assert part["partNumber"].split(".")[1] == B_PILLAR_CODE


def test_component_filter_actually_excludes_other_components():
    """Light, recent non-B-pillars exist -- the component filter must drop them."""
    search = _coarse_search()
    all_components = search(component=None, max_weight_kg=6.0, created_within_years=2)
    bpillars_only = search(component="B-pillar", max_weight_kg=6.0, created_within_years=2)
    assert all_components["matchCount"] > bpillars_only["matchCount"]


def test_demo_query_returns_the_expected_batch():
    result = _coarse_search()(component="B-pillar", max_weight_kg=6.0, created_within_years=2)
    assert result["matchCount"] == 8
    assert HERO in [p["partNumber"] for p in result["parts"]]
    for part in result["parts"]:
        assert part["weightKg"] <= 6.0
    weights = [p["weightKg"] for p in result["parts"]]
    assert weights == sorted(weights), "lightest first"


def test_created_within_years_is_relative_to_today():
    from datetime import date, timedelta  # noqa: PLC0415

    result = _coarse_search()(component="B-pillar", created_within_years=2)
    cutoff = date.fromisoformat(result["filtersApplied"]["created_after"])
    assert abs((date.today() - cutoff).days - 730) <= 2
    for part in result["parts"]:
        assert part["createdDate"] >= cutoff.isoformat()


def test_vehicle_model_filter():
    result = _coarse_search()(component="B-pillar", vehicle_model="10A")
    assert result["matchCount"] > 0
    for part in result["parts"]:
        assert part["vehicleModel"] == "10A"


def test_unknown_component_is_reported_not_guessed():
    result = _coarse_search()(component="flux capacitor")
    assert "error" in result
    assert result["matchCount"] == 0


def test_coarse_search_carries_no_feature_data():
    """Coarse results must not contain anything drawing-derived."""
    result = _coarse_search()(component="B-pillar", max_weight_kg=6.0, created_within_years=2)
    for part in result["parts"]:
        assert "hasSoftFoot" not in part and "hvValues" not in part


# --------------------------------------------------------------------------
# Drawing OCR and the soft-foot rule
# --------------------------------------------------------------------------

def test_ocr_is_reproducible():
    assert get_drawing_ocr(HERO)["ocrText"] == get_drawing_ocr(HERO)["ocrText"]


def test_extract_hv_values_reads_the_callouts():
    assert extract_hv_values(["HARDNESS ZONE A: 480 +/- 30 HV10"]) == [480]
    assert extract_hv_values(["480 +/- 30 HV10", "200 +/- 20 HV10"]) == [480, 200]
    assert extract_hv_values(["NO HARDNESS SPECIFIED"]) == []


def test_two_distinct_hv_values_mean_soft_foot():
    verdict = evaluate_soft_foot(["HARDNESS ZONE A: 480 +/- 30 HV10", "HARDNESS ZONE C: 200 +/- 20 HV10"])
    assert verdict["hasSoftFoot"] is True
    assert verdict["hvValues"] == [480, 200]
    assert len(verdict["evidence"]) == 2


def test_single_hv_value_means_no_soft_foot():
    verdict = evaluate_soft_foot(["HARDNESS (ALL ZONES): 480 +/- 30 HV10"])
    assert verdict["hasSoftFoot"] is False
    assert "uniform" in verdict["reason"]


def test_repeated_identical_hv_value_is_not_a_soft_foot():
    """Two callouts of the SAME hardness is still a uniform profile."""
    verdict = evaluate_soft_foot(["ZONE A: 480 HV10", "ZONE B: 480 HV10"])
    assert verdict["hasSoftFoot"] is False


def test_missing_hv_callout_is_reported_honestly():
    verdict = evaluate_soft_foot(["MATERIAL: 22MnB5", "SCALE 1:2"])
    assert verdict["hasSoftFoot"] is False
    assert "cannot be established" in verdict["reason"]


def test_exactly_one_part_in_the_catalog_has_a_soft_foot():
    assert len(SOFT_FOOT_PARTS) == 1
    with_soft_foot = [p["partNumber"] for p in _catalog() if analyze_drawing(p["partNumber"])["hasSoftFoot"]]
    assert with_soft_foot == [HERO]


def test_analyze_drawing_returns_evidence_lines():
    result = analyze_drawing(HERO)
    assert result["hasSoftFoot"] is True
    assert result["hvValues"] == [480, 200]
    assert all("HV" in line for line in result["evidence"])


def test_ocr_noise_never_corrupts_a_hardness_callout():
    """Prose may be degraded by 'OCR'; the HV lines must stay parseable."""
    for part in _catalog():
        ocr = get_drawing_ocr(part["partNumber"])
        assert extract_hv_values(ocr["ocrText"]), f"{part['partNumber']} lost its HV callout"


# --------------------------------------------------------------------------
# Fine search
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fine_search_lists_every_screened_part_not_just_survivors():
    candidates = [p["partNumber"] for p in _coarse_search()(component="B-pillar", max_weight_kg=6.0, created_within_years=2)["parts"]]
    result = await run_fine_search(candidates)
    assert result["screenedCount"] == len(candidates)
    assert result["partsWithSoftFoot"] == [HERO]
    assert len(result["screened"]) == len(candidates), "the engineer reviews the full list"


@pytest.mark.asyncio
async def test_fine_search_puts_qualifying_parts_first():
    result = await run_fine_search(["15A.507.640", HERO, "21B.507.658"])
    assert result["screened"][0]["partNumber"] == HERO


@pytest.mark.asyncio
async def test_fine_search_reports_catalog_attributes_alongside_the_verdict():
    result = await run_fine_search([HERO])
    entry = result["screened"][0]
    for field in ("weightKg", "createdDate", "vehicleModel", "hasSoftFoot", "hvValues", "evidence"):
        assert entry[field] is not None, field


@pytest.mark.asyncio
async def test_fine_search_handles_an_empty_batch():
    assert "error" in await run_fine_search([])


# --------------------------------------------------------------------------
# Stamping simulation
# --------------------------------------------------------------------------

def test_stamping_simulation_flags_many_regions_but_few_are_agent_fixable():
    """The core premise of the demo."""
    result = run_stamping_simulation(HERO)
    assert result["status"] == "fail"
    assert result["criticalAreaCount"] == 7
    assert result["agentFixableCount"] == 1
    assert result["engineerOnlyCount"] == 6


def test_the_agent_fixable_region_is_a_hole_with_a_parametric_fix():
    region = run_stamping_simulation(HERO)["criticalAreas"][0]
    assert region["regionId"] == "R-STM-01"
    assert region["regionType"] == "hole"
    assert region["agentFixable"] is True
    assert region["parametricOp"] == "increase_hole_radius"
    assert region["suggestedValueMm"] > region["currentValueMm"]


def test_critical_regions_are_reported_worst_first():
    severities = [a["severity"] for a in run_stamping_simulation(HERO)["criticalAreas"]]
    assert severities == sorted(severities, reverse=True)


def test_every_region_carries_a_suggested_fix():
    for area in run_stamping_simulation(HERO)["criticalAreas"]:
        assert area["suggestedFix"]
        assert {"regionId", "regionType", "location", "severity", "description", "agentFixable"} <= set(area)


@pytest.mark.parametrize(
    "fn,analysis",
    [(run_stamping_simulation, "stamping"), (run_stiffness_analysis, "stiffness"), (run_modal_analysis, "modal")],
)
def test_analyses_share_the_unified_schema(fn, analysis):
    result = fn(HERO)
    assert result["analysisType"] == analysis
    assert result["status"] in ("pass", "fail")
    assert isinstance(result["criticalAreas"], list)
    assert "metrics" in result and "summary" in result


def test_unknown_part_gets_generic_profile_not_a_crash():
    result = run_stamping_simulation("99Z.507.999")
    assert result["status"] in ("pass", "fail")
    assert result["partId"] == "99Z.507.999"


def test_engineer_worklist_contains_only_non_fixable_regions():
    worklist = list_engineer_rework_items(HERO, "stamping")
    assert worklist["itemCount"] == 6
    fixable_ids = {a["regionId"] for a in twin.agent_fixable_areas(HERO, "stamping")}
    assert not fixable_ids & {i["regionId"] for i in worklist["worklist"]}
    for item in worklist["worklist"]:
        assert item["suggestedFix"]


def test_engineer_worklist_rejects_unknown_analysis():
    assert "error" in list_engineer_rework_items(HERO, "telepathy")


# --------------------------------------------------------------------------
# Geometry: what the agent may and may not do
# --------------------------------------------------------------------------

def test_propose_is_read_only():
    before = run_stamping_simulation(HERO)["maxSeverity"]
    proposal = propose_geometry_change(HERO, "stamping")
    assert proposal["applied"] is False
    assert run_stamping_simulation(HERO)["maxSeverity"] == before


def test_propose_targets_the_hole_and_hands_over_the_rest():
    proposal = propose_geometry_change(HERO, "stamping")
    assert proposal["proposal"]["targetRegion"] == "R-STM-01"
    assert proposal["proposal"]["featureId"] == "HOLE_D13_5_LH"
    assert proposal["outOfScopeCount"] == 6
    for item in proposal["outOfScope"]:
        assert item["suggestedFix"]


def test_propose_refuses_a_region_it_cannot_fix_and_says_why():
    proposal = propose_geometry_change(HERO, "stamping", region_id="R-STM-02")
    assert proposal["proposal"] is None
    assert "CAD engineer rework" in proposal["message"]


def test_apply_resolves_the_hole_region():
    result = apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    assert result["status"] == "success"
    assert result["regionResolved"] is True
    assert result["featureId"] == "HOLE_D13_5_LH"
    assert result["remainingAgentFixable"] == 0
    assert result["remainingEngineerOnly"] == 6


def test_apply_refuses_an_engineer_only_region():
    result = apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-02", "stamping")
    assert result["status"] == "failure"
    assert "not fixable by a parametric change" in result["reason"]
    assert twin.max_severity(HERO, "stamping") == 0.68, "state must be untouched"


def test_apply_refuses_an_unsupported_operation():
    result = apply_modification_workflow(HERO, "reshape_flange", "R-STM-01", "stamping")
    assert result["status"] == "failure"
    assert "not a supported parametric operation" in result["reason"]


def test_apply_refuses_an_unknown_region():
    result = apply_modification_workflow(HERO, "increase_hole_radius", "R-NOPE-99", "stamping")
    assert result["status"] == "failure"
    assert "not found" in result["reason"]


def test_apply_refuses_an_already_resolved_region():
    apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    again = apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    assert again["status"] == "failure"
    assert "already below the critical threshold" in again["reason"]


def test_fixing_the_hole_does_not_magically_fix_a_flange():
    """No cross-region coupling: a hole radius change fixes the hole, nothing else."""
    before = {a["regionId"]: a["severity"] for a in run_stamping_simulation(HERO)["criticalAreas"]}
    apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    after = {a["regionId"]: a["severity"] for a in run_stamping_simulation(HERO)["criticalAreas"]}
    for region_id, severity in after.items():
        assert severity == before[region_id], f"{region_id} changed without being touched"


# --------------------------------------------------------------------------
# Manual CAD rework -- the human half
# --------------------------------------------------------------------------

def test_manual_rework_clears_the_engineer_only_regions():
    apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    result = record_manual_cad_rework(HERO, "stamping", note="reworked in CATIA")
    assert result["clearedCount"] == 6
    assert run_stamping_simulation(HERO)["status"] == "pass"


def test_manual_rework_leaves_an_open_agent_fixable_region_alone():
    """An unfixed agent region must not be silently absorbed into a handover."""
    result = record_manual_cad_rework(HERO, "stamping")
    assert "stamping/R-STM-01" not in result["clearedRegions"]
    assert len(twin.agent_fixable_areas(HERO, "stamping")) == 1


def test_manual_rework_can_target_specific_regions():
    result = record_manual_cad_rework(HERO, "stamping", region_ids=["R-STM-02", "R-STM-03"])
    assert result["clearedCount"] == 2
    assert run_stamping_simulation(HERO)["criticalAreaCount"] == 5


def test_manual_rework_rejects_unknown_analysis():
    assert "error" in record_manual_cad_rework(HERO, "telepathy")


def test_history_records_both_agent_and_manual_work():
    apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    record_manual_cad_rework(HERO, "stamping", note="rest done by hand")
    history = get_modification_history(HERO)
    assert history["agentModificationCount"] == 1
    assert history["manualReworkCount"] == 1
    assert history["currentRevision"] == "rev-01"


# --------------------------------------------------------------------------
# The loop: continue -> wait for engineer -> converged
# --------------------------------------------------------------------------

def test_loop_continues_while_agent_fixable_work_remains():
    loop_state.start_design_loop(HERO)
    status = loop_state.check_loop_status(HERO)
    assert status["shouldContinue"] is True
    assert status["agentFixableRemaining"] == 1
    assert status["terminated"] is False


def test_loop_blocks_on_manual_rework_rather_than_terminating():
    """The state that makes the handover possible: stopped, but NOT finished."""
    loop_state.start_design_loop(HERO)
    apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    loop_state.record_iteration(HERO, "hole fix", "success")

    status = loop_state.check_loop_status(HERO)
    assert status["shouldContinue"] is False
    assert status["terminated"] is False
    assert status["blockedOn"] == "manual_cad_rework"
    assert status["terminationReason"] is None
    assert status["engineerOnlyRemaining"] == 6


def test_loop_converges_after_the_engineer_reports_the_rework():
    loop_state.start_design_loop(HERO)
    apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    loop_state.record_iteration(HERO, "hole fix", "success")
    record_manual_cad_rework(HERO, "stamping", note="done")
    loop_state.record_manual_rework_round(HERO, "done")

    status = loop_state.check_loop_status(HERO)
    assert status["terminated"] is True
    assert status["terminationReason"] == "converged"
    assert status["blockedOn"] is None


def test_iteration_counter_is_deterministic_not_model_memory():
    loop_state.start_design_loop(HERO)
    for i in range(3):
        assert loop_state.record_iteration(HERO, f"mod {i}", "success")["iteration"] == i + 1


def test_termination_condition_max_iterations():
    loop_state.start_design_loop(HERO, max_iterations=2)
    loop_state.record_iteration(HERO, "mod 1", "success")
    loop_state.record_iteration(HERO, "mod 2", "success")
    status = loop_state.check_loop_status(HERO)
    assert status["terminated"] is True
    assert status["terminationReason"] == "max_iterations_reached"


def test_termination_condition_user_cancelled():
    loop_state.start_design_loop(HERO)
    loop_state.cancel_design_loop(HERO, reason="switching concept")
    status = loop_state.check_loop_status(HERO)
    assert status["terminated"] is True
    assert status["terminationReason"] == "user_cancelled"


def test_termination_condition_geometry_workflow_failed():
    loop_state.start_design_loop(HERO)
    loop_state.record_iteration(HERO, "bad modification", "failure")
    status = loop_state.check_loop_status(HERO)
    assert status["terminated"] is True
    assert status["terminationReason"] == "geometry_workflow_failed"


def test_loop_defaults_to_the_stamping_scope():
    loop_state.start_design_loop(HERO)
    assert loop_state.check_loop_status(HERO)["analysisScope"] == "stamping"


def test_unscoped_loop_requires_every_analysis():
    loop_state.start_design_loop(HERO, analysis_type=None)
    apply_modification_workflow(HERO, "increase_hole_radius", "R-STM-01", "stamping")
    record_manual_cad_rework(HERO, "stamping")
    status = loop_state.check_loop_status(HERO)
    assert status["analysisScope"] == "all"
    assert status["terminated"] is False, "stiffness still has an open region"


def test_start_design_loop_rejects_unknown_scope():
    assert "error" in loop_state.start_design_loop(HERO, analysis_type="telepathy")


def test_check_status_without_a_loop_is_safe():
    status = loop_state.check_loop_status("99Z.507.000")
    assert status["shouldContinue"] is False
    assert status["terminationReason"] == "no_active_loop"


def test_loop_history_records_agent_and_manual_steps():
    loop_state.start_design_loop(HERO, goal="clear stamping")
    loop_state.record_iteration(HERO, "increase_hole_radius on R-STM-01", "success")
    loop_state.record_manual_rework_round(HERO, "flanges reworked")
    history = loop_state.get_loop_history(HERO)
    assert history["goal"] == "clear stamping"
    assert history["iterationsCompleted"] == 1
    assert [h["outcome"] for h in history["history"]] == ["success", "reported"]


# --------------------------------------------------------------------------
# End to end, through the tools only
# --------------------------------------------------------------------------

def test_full_arc_agent_fix_then_handover_then_converged():
    loop_state.start_design_loop(HERO)
    assert run_stamping_simulation(HERO)["criticalAreaCount"] == 7

    proposal = propose_geometry_change(HERO, "stamping")["proposal"]
    applied = apply_modification_workflow(
        HERO, proposal["modificationType"], proposal["targetRegion"], "stamping"
    )
    assert applied["status"] == "success"
    loop_state.record_iteration(HERO, "hole fix", "success")
    assert run_stamping_simulation(HERO)["criticalAreaCount"] == 6

    blocked = loop_state.check_loop_status(HERO)
    assert blocked["blockedOn"] == "manual_cad_rework" and blocked["terminated"] is False

    record_manual_cad_rework(HERO, "stamping", note="reworked in CATIA")
    loop_state.record_manual_rework_round(HERO, "reworked in CATIA")

    assert run_stamping_simulation(HERO)["status"] == "pass"
    assert loop_state.check_loop_status(HERO)["terminationReason"] == "converged"


# --------------------------------------------------------------------------
# The KVS server really speaks MCP
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kvs_server_speaks_mcp_over_stdio():
    """Driven by the MCP client library so the protocol handshake is real."""
    from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
    from mcp.client.stdio import stdio_client  # noqa: PLC0415

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "mcp_servers" / "kvs_mcp_server.py")],
        cwd=str(REPO_ROOT),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            names = {t.name for t in (await session.list_tools()).tools}
            assert {"search_kvs_coarse", "get_part_record", "fetch_drawing_ocr"} <= names

            called = await session.call_tool(
                "search_kvs_coarse",
                {"component": "B-pillar", "max_weight_kg": 6.0, "created_within_years": 2},
            )
            payload = json.loads(called.content[0].text)
            assert payload["matchCount"] == 8
            for part in payload["parts"]:
                assert part["partNumber"].split(".")[1] == B_PILLAR_CODE

            drawing = await session.call_tool("fetch_drawing_ocr", {"part_number": HERO})
            ocr = json.loads(drawing.content[0].text)
            assert any("HV" in line for line in ocr["ocrText"])
