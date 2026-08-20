"""Proof that the Part Search Agent really uses MCP.

The KVS coarse search is a named demo goal and must be a genuine MCP
integration, not a plain function dressed up as one. DevUI's entity listing
shows only statically-known tools, so the MCP tools do not appear there --
they are discovered when the agent connects to the server at run time. These
tests check that they do.

A stub chat client stands in for the model, so no Azure credentials are needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_framework import Agent, ChatResponse, Content, Message

from common.llm_client import describe_configuration
from tests.test_approval_gate import StubChatClient

# Constructing an Agent requires a real chat client, so these two tests need the
# Foundry env vars set (any syntactically valid values will do -- the stub client
# replaces the real one before any network call). Everything else in the suite
# runs without configuration.
requires_config = pytest.mark.skipif(
    not describe_configuration()["configured"],
    reason="AZURE_AI_PROJECT_ENDPOINT / AZURE_AI_MODEL_DEPLOYMENT_NAME not set",
)


class ToolRecordingClient(StubChatClient):
    """Records the tool set the agent offers the model, then replies with text."""

    def __init__(self) -> None:
        super().__init__("unused", {})
        self.seen_tools: list[str] = []

    async def _inner_get_response(  # type: ignore[override]
        self, *, messages, stream: bool, options, **kwargs: Any
    ) -> ChatResponse:
        self.seen_tools = [getattr(t, "name", str(t)) for t in (options.get("tools") or [])]
        return ChatResponse(messages=[Message(role="assistant", contents=[Content.from_text("ok")])])


@requires_config
@pytest.mark.asyncio
async def test_agent_discovers_kvs_tools_over_mcp_at_run_time():
    from agents.part_search.agent import agent  # noqa: PLC0415

    recorder = ToolRecordingClient()
    original_client = agent.client
    agent.client = recorder
    try:
        async with agent:
            await agent.run("hello")
    finally:
        agent.client = original_client

    # Served by the MCP server subprocess, not imported in-process.
    assert "search_kvs_coarse" in recorder.seen_tools
    assert "get_part_record" in recorder.seen_tools
    assert "fetch_drawing_ocr" in recorder.seen_tools
    # The in-process fine-search tools are there too.
    assert "analyze_drawing" in recorder.seen_tools
    assert "run_fine_search" in recorder.seen_tools


def test_part_search_agent_module_does_not_import_the_server_code():
    """The agent must talk MCP to the server, not import its functions."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "agents"
        / "part_search"
        / "agent.py"
    ).read_text(encoding="utf-8")
    assert "MCPStdioTool" in source
    assert "from kvs_mcp_server import" not in source
    assert "import kvs_mcp_server" not in source
