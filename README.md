# Vehicle Design Copilot — Multi-Agent Demo

A local, runnable demo of a **four-agent system** built on the **Microsoft Agent
Framework** (Python) and inspected through **DevUI**, its local developer
dashboard.

An Orchestrator coordinates three specialists — Part Search, Simulation and
Geometry — to find a B-pillar in the KVS PLM system, run a stamping simulation
on it, and work through the critical regions: the agent fixes what a parametric
change can fix, a human approves it, and a CAD engineer takes the rest.

> **First-iteration demo scaffold.** Every backend integration (KVS, drawing
> OCR, parametric CAD workflow, CAE solver) is synthetic. Review and validate
> before using any output in a client-facing setting.

---

## What it demonstrates

| Demo goal | How this build demonstrates it |
|---|---|
| Foundry Hosted Agents | A single Foundry model deployment backs all four agents. Hosting the *agents themselves* in Foundry Agent Service is a documented next step, not built here. |
| Connected Agents architecture | `agent.as_tool()` wiring, orchestrator → three specialists. |
| Tool calling | Every specialist has explicit function tools. |
| MCP integration | KVS runs in a separate process, reached over a real stdio MCP connection: coarse part search, master records, drawing retrieval. |
| Multi-agent orchestration | Orchestrator + 3 specialists, visible in DevUI's trace view. |
| Human-in-the-loop governance | Two distinct human steps: `approval_mode="always_require"` gates every agent geometry change (owned by the Orchestrator — see below), and the loop **pauses and waits** for a CAD engineer to rework what the agent cannot. |
| Iterative engineering validation | Simulate → recommend → approve → apply → re-simulate → hand over → re-simulate, with the continue/wait/stop decision made deterministically in `tools/loop_state.py`. |

**One model deployment, four agents.** `common/llm_client.py` builds exactly one
`FoundryChatClient` and caches it; every agent is constructed with that same
instance. No agent creates a client of its own.

---

## The scenario

Part numbers in KVS look like **`10A.507.109`**:

| Segment | Meaning |
|---|---|
| `10A` | Vehicle model |
| `507` | Component class — **507 is the B-pillar** |
| `109` | Part id |

An engineer asks for *a B-pillar with a soft foot, at most 6 kg, created in the
last two years*. Two search stages answer that, and only the second one can:

- **Coarse (cheap, over MCP).** KVS filters on the component segment, weight and
  creation date. It holds **no feature data at all** — nothing in the catalog
  says whether a part has a soft foot.
- **Fine (expensive, per part).** Each candidate's drawing is retrieved from KVS
  and OCR'd, then a rule decides. About a second per drawing.

**The soft-foot rule:** a drawing showing **two or more different HV hardness
values** indicates a tailored hardness profile — a hardened upper section and a
soft foot. A single uniform HV value means no soft foot.

```
HARDNESS ZONE A (UPPER SECTION): 480 +/- 30 HV10     <- two different values
HARDNESS ZONE C (FOOT AREA):     200 +/- 20 HV10     <- => soft foot
```

In this iteration that rule is applied **deterministically in code**. In a
production system the OCR text would go to a general-purpose LLM together with a
description of the rule; the rule lives in one function
(`tools/drawing_analysis.py::evaluate_soft_foot`) so it can be swapped for that
call later.

---

## Prerequisites

- **Python 3.11+**
- **An Azure AI Foundry project** with a chat-capable model deployed (e.g. `gpt-4o`).
- **Authentication** via `DefaultAzureCredential` — run `az login` locally, or
  rely on a managed identity when hosted. No API key is stored in this repo.

## Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Two dependency constraints matter and are easy to get wrong — `requirements.txt`
pins both:

- **`mcp` must stay `<2`.** Agent Framework's MCP client does not speak the
  mcp SDK 2.x handshake (`InitializeResult.protocolVersion` moved), and fails
  at connect time with a confusing `AttributeError`.
- **The Foundry package is `agent-framework-foundry`** (class
  `FoundryChatClient`). The older `agent-framework-azure-ai` preview
  (`AzureAIAgentClient`) is stale and no longer imports against
  `agent-framework-core` 1.14.

## Configure

Export both variables in your shell — no `.env` file is required (one is loaded
if present, and never overrides your shell; see `.env.example`):

```bash
export AZURE_AI_PROJECT_ENDPOINT='https://<your-resource>.services.ai.azure.com/api/projects/<your-project>'
export AZURE_AI_MODEL_DEPLOYMENT_NAME='gpt-4o'
az login
```

## Run

```bash
python run_devui.py
```

DevUI opens at <http://127.0.0.1:8080> with all four agents registered. Useful flags:

| Flag | Effect |
|---|---|
| `--port 8090` | Serve on a different port. |
| `--no-browser` | Don't open a browser window. |
| `--tracing` | Enable OpenTelemetry instrumentation in DevUI. |
| `--auth` | Require a bearer token (auto-generated and logged). Off by default, which DevUI permits for loopback-only local development. |

Run the tests (no Azure credentials needed):

```bash
python -m pytest tests/ -q
```

Check the demo mechanics without Azure at all:

```bash
python offline_demo.py
```

---

## Demo walk-through

Select **OrchestratorAgent** in DevUI. Everything below is actual tool output.

The orchestrator supports three entry points — a search on its own, a simulation
on a part you already have (*"run a stamping simulation on 10A.507.109"*), or the
full flow below.

### 1. Find the part

> **I'm looking for a B-pillar that has a soft foot, weighs at most 6 kg, and was created in the last 2 years.**

**Coarse search over MCP** filters the 50-part catalog down to 8 B-pillars.
Fifteen parts meet the weight and date criteria, but seven of them are A-pillars,
door inners and side sills — the component segment excludes them.

**Fine search** then OCRs all 8 drawings (~8 s) and applies the HV rule. Every
screened part is reported, not just the survivors, so the engineer can see what
was checked and why each was ruled in or out:

| Part | kg | Created | Soft foot | HV values |
|---|---|---|---|---|
| **10A.507.109** | 5.21 | 2026-04-26 | **YES** | 480, 200 |
| 15A.507.640 | 4.15 | 2025-12-18 | no | 480 |
| 10A.507.979 | 4.62 | 2025-06-24 | no | 495 |
| 21B.507.658 | 4.98 | 2026-02-21 | no | 450 |
| 10A.507.893 | 5.40 | 2025-02-06 | no | 470 |
| 12D.507.290 | 5.63 | 2025-07-23 | no | 470 |
| 22A.507.591 | 5.85 | 2025-10-07 | no | 450 |
| 10B.507.427 | 5.94 | 2025-10-27 | no | 450 |

Note that the lightest part is *not* the answer — only `10A.507.109` carries two
hardness callouts, and that is invisible to the catalog search.

### 2. Pick a candidate

> **Let's go with 10A.507.109.**

### 3. Run the stamping simulation

> **Run a stamping simulation on it.**

It fails, with **seven** critical regions — and this is the point of the demo:

```
[AGENT  ] R-STM-01  hole    sev=0.68  Fastening hole D13.5, lower foot area
            -> Increase hole radius from 6.75 mm to 8.50 mm
[CAD ENG] R-STM-02  flange  sev=0.61  Upper weld flange, outboard edge
            -> Rework flange geometry / revise blank holder layout in CATIA
[CAD ENG] R-STM-03  wall    sev=0.57  Draw wall, mid-section inboard
[CAD ENG] R-STM-04  radius  sev=0.54  Transition radius, zone B
[CAD ENG] R-STM-05  bead    sev=0.47  Draw bead, rear die face
[CAD ENG] R-STM-06  flange  sev=0.41  Lower foot flange, trim edge
[CAD ENG] R-STM-07  wall    sev=0.36  Side wall near soft-zone boundary
```

**One** of the seven is agent-fixable: a hole attached to a named CATIA feature
whose fix is a parameter change. The other six need a CAD engineer. The
Simulation Agent always reports that split explicitly.

### 4. The approval gate ⛔

The Geometry Agent proposes `increase_hole_radius` on feature `HOLE_D13_5_LH`,
6.75 mm → 8.50 mm, and lists the six regions it cannot touch. The **Orchestrator**
then applies it — the Geometry Agent recommends but has no tool that can apply
(see [Where the approval gate lives](#where-the-approval-gate-lives)).

> **Apply that change.**

The run **pauses.** DevUI renders an **Approve / Reject** panel showing the tool
and its arguments. Nothing has been modified yet.

- **Reject** → nothing is applied and the agent says so.
- **Approve** → the workflow runs and returns a job id and model revision.

Re-simulation shows **6** regions left, `0` of them agent-fixable. Notably the
other six severities are unchanged — a hole radius change fixes the hole and
nothing else, which is asserted in the tests.

### 5. The handover — the second human step 👷

`check_loop_status` now returns something that is neither "continue" nor
"finished":

```
shouldContinue: false
terminated:     false
blockedOn:      "manual_cad_rework"
```

So the orchestrator hands over the worklist — each region with its location and
suggested fix — and asks the engineer to make those changes in CATIA. Then it
**waits**.

> **I've made those changes in CATIA — changes applied.**

The orchestrator records the rework and re-runs the simulation.

### 6. Convergence

```
Stamping simulation PASSED for 10A.507.109: no critical regions remain.
Termination condition: converged
```

The orchestrator names which of the four termination conditions fired:

| Reason | Meaning |
|---|---|
| `converged` | No critical regions remain in the loop's scope. |
| `max_iterations_reached` | Iteration cap hit (default 5) with issues still open. |
| `user_cancelled` | The engineer stopped the loop. |
| `geometry_workflow_failed` | The geometry workflow reported a failure. |

**Other things worth trying:** ask the Geometry Agent to fix `R-STM-02` and watch
it refuse with a reason; reject an approval to see the gate hold; run the
specialists standalone; ask to stop mid-loop to trigger `user_cancelled`.

---

## Where the approval gate lives

`apply_modification_workflow` is gated with `approval_mode="always_require"` and
sits on the **Orchestrator**, not on the Geometry Agent. That looks like a layering
mistake and is in fact required for the gate to work at all.

`agent.as_tool()` runs a sub-agent as a **stateless function call**. If a tool
inside that sub-agent needs approval, the framework raises
`UserInputRequiredException`; the sub-agent run is abandoned and the approval
request is re-tagged with the *parent's* call id and shown to the user. Approving
resumes the parent, which re-invokes the sub-agent **from scratch**. With no
memory of the pending approval it repeats the same call and hits the gate again.

The result is an endless approve → re-ask loop in which the change is never
applied, and the sub-agent appears to be called over and over in the UI.
`propagate_session=True` does not help.

Putting the gated tool on the agent that owns the conversation fixes it: the
approval response matches that agent's own function call and resumes it.
`tests/test_approval_roundtrip.py` pins all of this down — that approving on the
Orchestrator applies the change exactly once, that the sub-agent arrangement
loops, and that no specialist holds a gated tool.

**Division of labour:** the Geometry Agent owns the *recommendation*; the
Orchestrator owns the *approved action*.

## Keeping the specialists from looping

Two structural rules, both because a tool menu is an invitation:

- **No per-drawing tool on the Part Search Agent.** The KVS MCP server exposes
  `fetch_drawing_ocr` for a single part, but `allowed_tools` keeps it off the
  model's menu. Offered alongside the batch `run_fine_search`, it invites the
  model to loop over candidates one at a time — eight calls and eight OCR delays
  for an eight-part batch. Drawings are reached only through the batch tool.
- **No gated tool inside any specialist**, per the section above.

- **Setup calls are idempotent and order-free.** `start_design_loop` returns
  `alreadyRunning` instead of resetting, and `check_loop_status` opens a session
  if none exists. Previously a re-issued `start_design_loop` silently wiped the
  iteration count and history, so `max_iterations_reached` could never fire and
  the loop was unbounded — visible in DevUI as a stream of identical
  `start_design_loop` calls all reporting `iteration: 0`. An order-dependent
  tool set is one a model can get stuck on.
- **A hard ceiling on tool calls per run.** The framework defaults to 40 model
  round-trips with *unlimited* calls per round-trip, so a stuck model can make
  hundreds of identical calls before anything stops it. `common/llm_client.py`
  sets `max_function_calls` to 30 — well above the full demo flow.

Note that `max_invocations` on an individual tool is *not* a usable backstop
here: the counter is lifetime-scoped and never reset per run, so with
module-level agents it would permanently disable a tool partway through a DevUI
session.

---

## What's real vs. mocked

| Component | Real or mocked | Production swap-in |
|---|---|---|
| LLM calls | **Real** — live Azure AI Foundry calls via `DefaultAzureCredential` | Unchanged; point at a production deployment. |
| Agent orchestration, tool calling, approval gate, MCP transport | **Real** Agent Framework machinery | Unchanged. |
| KVS catalog + coarse search | Mocked — `mock_data/kvs_catalog.json`, served over a real MCP server | Replace `_load_catalog()` with the KVS query API. The agent side does not change. |
| Drawing retrieval + OCR | Mocked — fabricated drawing text in `mock_data/drawing_ocr.py` | Fetch the real drawing PDF from KVS and OCR it (Azure Document Intelligence, Tesseract). |
| Soft-foot determination | Mocked — deterministic HV rule in `tools/drawing_analysis.py` | Hand the OCR text plus the rule to a general-purpose LLM. Deliberately **not** done in this iteration, to keep the demo reproducible. |
| Stamping simulation | Mocked — `tools/simulation_tools.py` reads the twin | Submit to the real forming solver (AutoForm / LS-DYNA) and parse results into the same schema, including the agent-fixable classification. |
| Parametric geometry change | Mocked — `tools/geometry_tools.py` mutates an in-memory twin | Call the real CATIA/NX automation service. **The approval gate stays exactly where it is** — on the Orchestrator. |
| Manual CAD rework | Mocked — the engineer's chat confirmation clears the regions | Same handover, but the engineer's actual CATIA revision would be checked back into PLM and re-simulated. |
| Agent hosting | Local process + DevUI | Deploy these same agents to Foundry Agent Service. |

### State and persistence

Loop state and the digital twin live in **module-level Python dicts**
(`mock_data/digital_twin.py`, `tools/loop_state.py`). They exist for the life of
the process and reset on restart — deliberate for a single-process local demo,
and called out in both files. Nothing outside those modules touches the dicts
directly, so swapping in a JSON file, SQLite or Redis is a local change behind
the same accessor functions. **This is the first thing to replace** if state must
survive restarts or be shared across replicas.

---

## Repo layout

```
common/llm_client.py            The one shared FoundryChatClient (cached)
mcp_servers/kvs_mcp_server.py   stdio MCP server: KVS search, records, drawings
mock_data/
  kvs_catalog.json              50 synthetic parts (40 B-pillars + 10 decoys)
  drawing_ocr.py                Fabricated drawing OCR text per part
  digital_twin.py               In-memory simulation state, typed regions
tools/
  drawing_analysis.py           Fine search: HV rule, soft-foot verdict
  simulation_tools.py           Stamping simulation + stiffness/modal
  geometry_tools.py             Propose, apply (gated), record manual rework
  loop_state.py                 Continue / wait-for-engineer / stop, in code
agents/
  part_search/agent.py          Coarse (MCP) + fine search
  simulation/agent.py           CAE analyses and the fixability split
  geometry/agent.py             Recommends changes; cannot apply them
  orchestrator/agent.py         Wires specialists via .as_tool(); owns the gate
run_devui.py                    Registers all four agents in DevUI
offline_demo.py                 Walk-through mechanics without Azure
tests/                          81 tests; no credentials required
```

---

## Scope of this iteration

Assumptions made in building this — flagged rather than silently adopted:

- **Local only.** Runs and is inspected through DevUI on localhost. No
  deployment to Foundry Agent Service hosted agents.
- **In-memory state.** Resets on restart, as described above.
- **All backends synthetic.** No real KVS, CATIA, or solver is contacted.
- **No LLM inference in the fine search.** The soft-foot rule is deterministic
  code in this iteration, by choice.
- **Exactly one catalog part has a soft foot**, so the demo has one unambiguous
  answer.
- **Catalog dates are relative to when the catalog was generated** (`_generatedFor` in `kvs_catalog.json`). The "last 2 years" query depends on that window; regenerate the catalog if today drifts far past it.
- **The soft-foot rule is approximate.** "Two different HV values" is a working
  heuristic, not a validated engineering definition. It is isolated in one
  function so it can be corrected.
- **The gate sits on the Orchestrator**, not the Geometry Agent, because a gated
  tool inside an `as_tool()` sub-agent can never complete its approval
  round-trip. Explained above and pinned by tests.
- **Approval on applying, not proposing.** `propose_geometry_change` is ungated
  because recommending is not changing. `record_manual_cad_rework` is also
  ungated: it records work a human says they already did, and the gate exists to
  supervise the *agent*, not the engineer.
- **Sub-agent calls are ungated.** Gating the whole `as_tool()` call would ask
  the engineer to approve merely *consulting* a specialist.
