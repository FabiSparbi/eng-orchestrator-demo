"""Part Search Agent -- finds replacement candidates in the (mock) ePLM system.

Two-stage search, exactly as the design brief specifies:

  1. COARSE, over MCP: attribute-only filtering (material, weight, publication
     date) against the ePLM catalog. This runs in a separate process and is
     reached over a real stdio MCP connection -- see mcp_servers/.
  2. FINE, in-process: drawing analysis to compare mounting interfaces, which
     is what actually decides whether a candidate bolts into the same place.

Both are then combined into the ranked-candidate schema by rank_candidates.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_framework import Agent, MCPStdioTool

from common.llm_client import get_shared_chat_client
from tools.drawing_analysis import analyze_drawing, compare_mounting_interfaces
from tools.part_search_tools import rank_candidates

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The MCP integration point. The agent does not import the server's code; it
# launches it as a subprocess and speaks MCP to it over stdio.
eplm_mcp = MCPStdioTool(
    name="eplm",
    command=sys.executable,
    args=[str(REPO_ROOT / "mcp_servers" / "eplm_mcp_server.py")],
    description=(
        "Mock ePLM system. Coarse attribute search over the part catalog: "
        "filter by material, maximum weight and publication date."
    ),
)

INSTRUCTIONS = """You are the Part Search Agent for a vehicle engineering team.

Your job is to find existing parts in the ePLM system that could replace a
given reference part, and to rank them.

Always search in two stages, in this order:

1. COARSE SEARCH (ePLM, via the `search_eplm_coarse` tool): filter on
   attributes only -- material, maximum weight, publication date. This stage
   has no access to geometry or drawings, so never claim anything about fit
   based on it. If the user asks for something "lighter", use the reference
   part's weight as the max_weight_kg ceiling. Use `get_part_record` to look up
   the reference part's own attributes when you need them.

2. FINE SEARCH (drawing analysis): for the coarse hits, call `analyze_drawing`
   and `compare_mounting_interfaces` against the reference part to establish
   whether the mounting interface actually matches. A part that is lighter but
   has a different bolt pattern is NOT a valid candidate, and you must say so
   explicitly rather than quietly dropping it.

3. RESULT: call `rank_candidates` with the reference part and the coarse hits
   to produce the final ranked list. Report the ranked candidates with their
   part number, match score, weight saving, mounting-pattern verdict and the
   reason for the ranking. Include the confidence value -- these results come
   from automated drawing analysis and are advisory.

Be concise and factual. Report what the tools returned; never invent part
numbers, weights or geometry. If no candidate fits, say so plainly."""

agent = Agent(
    name="PartSearchAgent",
    description=(
        "Searches the ePLM system for replacement part candidates using coarse "
        "attribute filtering plus fine drawing-based mounting analysis, and "
        "returns them ranked."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[eplm_mcp, analyze_drawing, compare_mounting_interfaces, rank_candidates],
)
