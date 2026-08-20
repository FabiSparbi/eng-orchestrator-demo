"""Where the human-in-the-loop gate may live, and why.

A gated tool works only if it belongs to the agent that owns the conversation
with the user. Put it inside a sub-agent invoked through `as_tool()` and the
approval can never complete:

  * `as_tool()` runs the sub-agent as a stateless function call.
  * When a tool inside it needs approval, the framework raises
    UserInputRequiredException; the sub-agent run is ABANDONED and the approval
    request is re-tagged with the parent's call id and shown to the user.
  * Approving resumes the PARENT, which re-invokes the sub-agent from scratch.
    It has no memory of the pending approval, so it repeats the same tool call
    and hits the gate again.

Net effect: an endless approve -> re-ask loop in which the change is never
applied, and the sub-agent appears to be called over and over in the UI.

These tests use stub chat clients, so no Azure credentials and no real LLM are
involved. They pin the constraint that put `apply_modification_workflow` on the
Orchestrator rather than the Geometry Agent.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
    tool,
)

from common.llm_client import describe_configuration
from mock_data import digital_twin as twin
from tools.geometry_tools import apply_modification_workflow

HERO = "10A.507.109"
APPLY_ARGS = {
    "part_id": HERO,
    "modification_type": "increase_hole_radius",
    "region_id": "R-STM-01",
    "analysis_type": "stamping",
}

requires_config = pytest.mark.skipif(
    not describe_configuration()["configured"],
    reason="AZURE_AI_PROJECT_ENDPOINT / AZURE_AI_MODEL_DEPLOYMENT_NAME not set",
)


class ScriptedClient(FunctionInvocationLayer, BaseChatClient):
    """Emits one scripted function call per turn, then closes out with text.

    Supports streaming, because `as_tool()` always runs its sub-agent with
    stream=True. Counts turns so a runaway loop is measurable.
    """

    def __init__(self, label: str, function_name: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self.label = label
        self.function_name = function_name
        self.arguments = arguments
        self.turns = 0

    def _next_contents(self, messages) -> list[Content]:
        self.turns += 1
        for message in reversed(messages):
            for content in getattr(message, "contents", []) or []:
                if getattr(content, "type", None) == "function_result":
                    return [Content.from_text(f"{self.label} finished.")]
        return [
            Content.from_function_call(
                call_id=f"{self.label}-{self.turns}",
                name=self.function_name,
                arguments=self.arguments,
            )
        ]

    def _inner_get_response(self, *, messages, stream: bool, options, **kwargs: Any):  # type: ignore[override]
        if stream:

            async def _updates():
                yield ChatResponseUpdate(role="assistant", contents=self._next_contents(messages))

            return ResponseStream(_updates(), finalizer=ChatResponse.from_updates)

        async def _response():
            return ChatResponse(messages=[Message(role="assistant", contents=self._next_contents(messages))])

        return _response()


def _contents(response, content_type: str) -> list:
    return [
        c
        for m in response.messages
        for c in (getattr(m, "contents", []) or [])
        if getattr(c, "type", None) == content_type
    ]


def _approval_requests(response) -> list:
    return _contents(response, "function_approval_request")


def _gated_apply_tool():
    return tool(
        apply_modification_workflow,
        name="apply_modification_workflow",
        approval_mode="always_require",
    )


async def _approve(agent, first_response, approved: bool = True):
    request = _approval_requests(first_response)[0]
    decision = Content.from_function_approval_response(
        approved=approved, id=request.id, function_call=request.function_call
    )
    return await agent.run([*first_response.messages, Message(role="user", contents=[decision])])


@pytest.fixture(autouse=True)
def clean_twin():
    twin.reset_all()
    yield
    twin.reset_all()


# --------------------------------------------------------------------------
# The arrangement we ship: gated tool on the agent that talks to the user
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate_on_the_conversational_agent_applies_after_approval():
    """The requirement: approving must actually apply the change, once."""
    client = ScriptedClient("ORCH", "apply_modification_workflow", APPLY_ARGS)
    orchestrator = Agent(name="Orch", instructions="o", client=client, tools=[_gated_apply_tool()])

    first = await orchestrator.run("Apply the hole radius change.")
    assert _approval_requests(first), "expected the run to pause for approval"
    assert twin.find_region(HERO, "stamping", "R-STM-01")["severity"] == 0.68, "changed before approval"

    second = await _approve(orchestrator, first, approved=True)

    assert twin.find_region(HERO, "stamping", "R-STM-01")["severity"] < 0.68, (
        "approval did not reach the tool -- the change was never applied"
    )
    assert not _approval_requests(second), "gate re-fired after approval (approve -> re-ask loop)"


@pytest.mark.asyncio
async def test_gate_on_the_conversational_agent_honours_rejection():
    client = ScriptedClient("ORCH", "apply_modification_workflow", APPLY_ARGS)
    orchestrator = Agent(name="Orch", instructions="o", client=client, tools=[_gated_apply_tool()])

    first = await orchestrator.run("Apply the hole radius change.")
    await _approve(orchestrator, first, approved=False)

    assert twin.find_region(HERO, "stamping", "R-STM-01")["severity"] == 0.68


# --------------------------------------------------------------------------
# The arrangement we must NOT ship, pinned so it cannot creep back
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate_inside_a_subagent_never_completes():
    """Characterisation test for the framework behaviour driving our design.

    If this ever fails, the framework has started carrying approvals through
    `as_tool()` -- at which point revisit where the gate lives, because the
    sub-agent arrangement would become viable again.
    """
    geometry_client = ScriptedClient("GEOMETRY", "apply_modification_workflow", APPLY_ARGS)
    geometry = Agent(name="GeometryAgent", instructions="g", client=geometry_client, tools=[_gated_apply_tool()])

    orchestrator_client = ScriptedClient("ORCH", "geometry_agent", {"task": "fix the hole"})
    orchestrator = Agent(
        name="Orch",
        instructions="o",
        client=orchestrator_client,
        tools=[geometry.as_tool(name="geometry_agent")],
    )

    first = await orchestrator.run("Fix the hole.")
    assert _approval_requests(first), "the approval request should still reach the user"

    second = await _approve(orchestrator, first, approved=True)

    # Both halves of the failure: nothing applied, and the gate asks again.
    assert twin.find_region(HERO, "stamping", "R-STM-01")["severity"] == 0.68
    assert _approval_requests(second), "approve -> re-ask loop expected with a gated tool in a sub-agent"
    assert geometry_client.turns > 1, "the sub-agent was re-run from scratch"


# --------------------------------------------------------------------------
# Structural guarantees on the shipped agents
# --------------------------------------------------------------------------

def _tools_of(agent) -> list:
    """An Agent keeps its function tools in default_options and MCP tools apart."""
    return list(agent.default_options.get("tools") or []) + list(agent.mcp_tools or [])


def _tool_names(agent) -> set[str]:
    return {getattr(t, "name", "") for t in _tools_of(agent)}


@requires_config
def test_orchestrator_owns_the_gated_apply_tool():
    from agents.orchestrator.agent import agent  # noqa: PLC0415

    gated = [t for t in _tools_of(agent) if getattr(t, "approval_mode", None) == "always_require"]
    assert [t.name for t in gated] == ["apply_modification_workflow"]


@requires_config
def test_geometry_agent_cannot_apply_geometry():
    """Structural, not advisory: the broken path must not exist."""
    from agents.geometry.agent import agent  # noqa: PLC0415

    assert "apply_modification_workflow" not in _tool_names(agent)
    assert "propose_geometry_change" in _tool_names(agent)


@requires_config
def test_no_gated_tool_hides_inside_any_specialist():
    from agents.geometry.agent import agent as geometry  # noqa: PLC0415
    from agents.part_search.agent import agent as part_search  # noqa: PLC0415
    from agents.simulation.agent import agent as simulation  # noqa: PLC0415

    for specialist in (geometry, part_search, simulation):
        gated = [t for t in _tools_of(specialist) if getattr(t, "approval_mode", None) == "always_require"]
        assert not gated, f"{specialist.name} holds a gated tool that as_tool() cannot round-trip"


@requires_config
@pytest.mark.asyncio
async def test_part_search_offers_no_per_drawing_tool():
    """Drawings are reached only through the batch fine search.

    A per-part drawing tool on the menu invites the model to loop over
    candidates one at a time -- eight calls and eight OCR delays for an
    eight-part batch, which reads as the search agent running away.
    """
    from agents.part_search.agent import agent  # noqa: PLC0415

    from tests.test_mcp_integration import ToolRecordingClient  # noqa: PLC0415

    recorder = ToolRecordingClient()
    original = agent.client
    agent.client = recorder
    try:
        async with agent:
            await agent.run("hello")
    finally:
        agent.client = original

    assert "run_fine_search" in recorder.seen_tools
    assert "search_kvs_coarse" in recorder.seen_tools
    assert "fetch_drawing_ocr" not in recorder.seen_tools, (
        "per-drawing MCP tool is exposed; restrict it with allowed_tools"
    )
    assert "analyze_drawing" not in recorder.seen_tools, (
        "per-part drawing tool is exposed alongside the batch fine search"
    )
