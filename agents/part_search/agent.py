"""Part Search Agent -- finds B-pillar candidates in the (mock) KVS PLM system.

Two-stage search:

  1. COARSE, over MCP: attribute-only filtering against the KVS catalog --
     component class (from the part number's middle segment), weight, creation
     date, vehicle model. Runs in a separate process, reached over a real stdio
     MCP connection. Cheap, and blind to features.
  2. FINE, in-process: each candidate's drawing is retrieved and OCR'd, then a
     rule decides whether the part has a SOFT FOOT. Expensive -- roughly a
     second per drawing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_framework import Agent, MCPStdioTool

from common.llm_client import get_shared_chat_client
from tools.drawing_analysis import run_fine_search

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The MCP integration point. The agent does not import the server's code; it
# launches it as a subprocess and speaks MCP to it over stdio.
#
# TOOL SURFACE IS DELIBERATELY NARROW.
# The server also exposes `fetch_drawing_ocr`, which returns ONE part's drawing
# text. Offering that to the model alongside the batch fine search invites it to
# loop over candidates one at a time -- eight sequential calls for an eight-part
# batch, each with the full OCR latency, which is what "the search agent keeps
# getting called" looks like from the UI. `allowed_tools` keeps the per-drawing
# entry point off the model's menu; drawings are reached only through
# `run_fine_search`, which handles the whole batch in one call.
kvs_mcp = MCPStdioTool(
    name="kvs",
    command=sys.executable,
    args=[str(REPO_ROOT / "mcp_servers" / "kvs_mcp_server.py")],
    allowed_tools=["search_kvs_coarse", "get_part_record"],
    description=(
        "Mock KVS PLM system. Coarse attribute search over the part catalog "
        "(component class, weight, creation date, vehicle model) and part "
        "master records."
    ),
)

INSTRUCTIONS = """You are the Part Search Agent for a vehicle engineering team.

You find parts in the KVS PLM system that match an engineer's requirements.

PART NUMBERS look like 10A.507.109:
  * first segment  = vehicle model (10A)
  * middle segment = component class -- 507 is the B-pillar
  * last segment   = part id

Always search in two stages, in this order:

1. COARSE SEARCH (KVS, via the `search_kvs_coarse` tool). Filter on attributes
   only: component, maximum weight, creation date, vehicle model. For "created
   in the last N years" use `created_within_years=N` rather than computing a
   date yourself. This stage knows nothing about a part's features -- never
   claim anything about a soft foot from coarse results.

2. FINE SEARCH (`run_fine_search`), passing ALL the part numbers the coarse
   search returned in ONE call. It retrieves each drawing, OCRs it and applies
   the soft-foot rule.

   Call it exactly once per batch. It costs roughly a second per drawing, so
   calling it repeatedly, or once per part, wastes the engineer's time for no
   extra information. It is also the ONLY way to reach drawing data -- there is
   no per-part drawing tool. To check a single part, pass a one-element list.
   Never call it twice with the same part numbers; the result does not change.

   THE SOFT-FOOT RULE: a drawing showing two or more different HV hardness
   values indicates a tailored hardness profile, i.e. a soft foot. A single
   uniform HV value means no soft foot. The tool applies this rule and returns
   the HV values and the exact drawing lines they came from.

3. RESULT: present every screened part as a table -- part number, weight,
   creation date, vehicle model, and whether it has a soft foot with the HV
   values found. Do not hide the parts that failed; the engineer wants to see
   what was checked and why each was ruled in or out. Then say clearly which
   part(s) meet the requirement and ask the engineer to choose.

Do NOT rank candidates by similarity to some reference part -- there is no
reference part. Report what the tools returned; never invent part numbers,
weights or drawing content. If nothing matches, say so plainly."""

agent = Agent(
    name="PartSearchAgent",
    description=(
        "Searches the KVS PLM system for B-pillar candidates: coarse attribute "
        "filtering over MCP, then drawing OCR to establish features such as a "
        "soft foot."
    ),
    instructions=INSTRUCTIONS,
    client=get_shared_chat_client(),
    tools=[kvs_mcp, run_fine_search],
)
