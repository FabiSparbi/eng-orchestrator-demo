"""Proof that the human-in-the-loop gate actually blocks execution.

Checking `approval_mode == "always_require"` on the tool object only proves it
is configured. These tests prove it *fires*: a stub chat client asks the agent
to call `apply_modification_workflow`, and the run must come back asking for
approval WITHOUT having touched the digital twin.

No Azure credentials and no real LLM are involved -- the stub chat client
stands in for the model.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    tool,
)

from mock_data import digital_twin as twin
from tools.geometry_tools import apply_modification_workflow, propose_geometry_change


class StubChatClient(FunctionInvocationLayer, BaseChatClient):
    """A chat client that emits one scripted function call, then stops.

    Stands in for the Foundry model so the approval machinery can be tested
    without network access or credentials.
    """

    def __init__(self, function_name: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self._function_name = function_name
        self._arguments = arguments
        self.call_count = 0

    async def _inner_get_response(  # type: ignore[override]
        self,
        *,
        messages,
        stream: bool,
        options,
        **kwargs: Any,
    ) -> ChatResponse:
        self.call_count += 1
        if self.call_count > 1:
            # Second turn (post-approval): just close out with text.
            return ChatResponse(
                messages=[Message(role="assistant", contents=[Content.from_text("Done.")])]
            )

        return ChatResponse(
            messages=[
                Message(
                    role="assistant",
                    contents=[
                        Content.from_function_call(
                            call_id="call-1",
                            name=self._function_name,
                            arguments=self._arguments,
                        )
                    ],
                )
            ]
        )


def _contents_of_type(response, content_type: str) -> list:
    """Pull contents of one `Content.type` out of an agent response."""
    found = []
    for message in response.messages:
        for content in getattr(message, "contents", []) or []:
            if getattr(content, "type", None) == content_type:
                found.append(content)
    return found


def _approval_requests(response) -> list:
    return _contents_of_type(response, "function_approval_request")


@pytest.fixture(autouse=True)
def clean_twin():
    twin.reset_all()
    yield
    twin.reset_all()


@pytest.mark.asyncio
async def test_apply_modification_pauses_for_approval_and_does_not_mutate():
    """The load-bearing test: geometry is NOT changed before a human approves."""
    gated = tool(
        apply_modification_workflow,
        name="apply_modification_workflow",
        approval_mode="always_require",
    )
    client = StubChatClient(
        "apply_modification_workflow",
        {
            "part_id": "10A.507.109",
            "modification_type": "increase_hole_radius",
            "region_id": "R-STM-01",
            "analysis_type": "stamping",
        },
    )
    agent = Agent(name="GateTest", instructions="Test agent.", client=client, tools=[gated])

    severity_before = twin.max_severity("10A.507.109", "stamping")
    response = await agent.run("Apply the hole radius change to 10A.507.109.")

    assert _approval_requests(response), (
        "Expected an approval request in the response; the run did not pause for approval."
    )
    assert twin.max_severity("10A.507.109", "stamping") == severity_before, (
        "Digital twin was mutated before approval -- the human-in-the-loop gate failed."
    )


@pytest.mark.asyncio
async def test_read_only_proposal_tool_runs_without_approval():
    """Recommending is not changing: propose_geometry_change must not be gated."""
    ungated = tool(propose_geometry_change, name="propose_geometry_change")
    client = StubChatClient(
        "propose_geometry_change",
        {"part_id": "10A.507.109", "analysis_type": "stamping"},
    )
    agent = Agent(name="ProposeTest", instructions="Test agent.", client=client, tools=[ungated])

    response = await agent.run("What do you recommend for 10A.507.109 stamping?")

    assert not _approval_requests(response), "propose_geometry_change must not require approval."
    assert client.call_count == 2, "Tool should have executed and returned to the model."


@pytest.mark.asyncio
async def test_approving_the_request_lets_the_change_through():
    """Approve -> the modification is applied and the twin improves."""
    gated = tool(
        apply_modification_workflow,
        name="apply_modification_workflow",
        approval_mode="always_require",
    )
    client = StubChatClient(
        "apply_modification_workflow",
        {
            "part_id": "10A.507.109",
            "modification_type": "increase_hole_radius",
            "region_id": "R-STM-01",
            "analysis_type": "stamping",
        },
    )
    agent = Agent(name="GateTest", instructions="Test agent.", client=client, tools=[gated])

    severity_before = twin.max_severity("10A.507.109", "stamping")
    first = await agent.run("Apply the hole radius change to 10A.507.109.")
    request = _approval_requests(first)[0]

    # Send the approval back, exactly as DevUI's Approve button does.
    approval = Content.from_function_approval_response(
        approved=True,
        id=request.id,
        function_call=request.function_call,
    )
    await agent.run(
        [*first.messages, Message(role="user", contents=[approval])]
    )

    assert twin.max_severity("10A.507.109", "stamping") < severity_before, (
        "Approved modification did not reach the digital twin."
    )


@pytest.mark.asyncio
async def test_rejecting_the_request_leaves_geometry_untouched():
    """Reject -> nothing is applied. The gate is not advisory."""
    gated = tool(
        apply_modification_workflow,
        name="apply_modification_workflow",
        approval_mode="always_require",
    )
    client = StubChatClient(
        "apply_modification_workflow",
        {
            "part_id": "10A.507.109",
            "modification_type": "increase_hole_radius",
            "region_id": "R-STM-01",
            "analysis_type": "stamping",
        },
    )
    agent = Agent(name="GateTest", instructions="Test agent.", client=client, tools=[gated])

    severity_before = twin.max_severity("10A.507.109", "stamping")
    first = await agent.run("Apply the hole radius change to 10A.507.109.")
    request = _approval_requests(first)[0]

    rejection = Content.from_function_approval_response(
        approved=False,
        id=request.id,
        function_call=request.function_call,
    )
    await agent.run(
        [*first.messages, Message(role="user", contents=[rejection])]
    )

    assert twin.max_severity("10A.507.109", "stamping") == severity_before, (
        "Geometry changed despite the user rejecting the approval request."
    )
