# Vehicle Design Copilot — Multi-Agent Demo

A local, runnable demo of a **four-agent system** built on the **Microsoft Agent
Framework** (Python) and inspected through **DevUI**, its local developer
dashboard.

One Orchestrator coordinates three specialists — Part Search, Geometry and
Simulation — through an iterative engineering loop, with a **human approval gate
on every geometry change**.

> **First-iteration demo scaffold.** Every backend integration (ePLM, drawing
> analysis, parametric CAD workflow, CAE solver) is synthetic. Review and
> validate before using any output in a client-facing setting.

---

## What it demonstrates

| Demo goal | How this build demonstrates it |
|---|---|
| Foundry Hosted Agents | A single Foundry model deployment backs all four agents. Hosting the *agents themselves* in Foundry Agent Service is a documented next step, not built here. |
| Connected Agents architecture | `agent.as_tool()` wiring, orchestrator → three specialists. |
| Tool calling | Every specialist has explicit function tools. |
| MCP integration | Part Search's coarse ePLM search runs in a separate process, reached over a real stdio MCP connection. |
| Multi-agent orchestration | Orchestrator + 3 specialists, visible in DevUI's trace view. |
| Human-in-the-loop governance | `approval_mode="always_require"` on `apply_modification_workflow`, surfaced natively by DevUI as Approve/Reject. |
| Iterative engineering validation | Simulate → recommend → approve → apply → re-simulate, with deterministic termination in `tools/loop_state.py` and a digital twin that visibly converges. |

**One model deployment, four agents.** `common/llm_client.py` builds exactly one
`FoundryChatClient` and caches it; every agent is constructed with that same
instance. No agent creates a client of its own.

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

Select **OrchestratorAgent** in DevUI and work through these prompts. Everything
below is what the tools actually produce.

### 1. Find a replacement part

> **Find a lighter bracket for Model X with similar mounting points to part BR-2201.**

The orchestrator calls **PartSearchAgent**, which searches in two stages:

- **Coarse (over MCP):** `search_eplm_coarse` filters the ePLM catalog on
  attributes only — material, weight, publication date. This tool lives in a
  separate process (`mcp_servers/eplm_mcp_server.py`) and is reached over stdio
  MCP. It has no access to geometry, by design.
- **Fine (in-process):** `analyze_drawing` and `compare_mounting_interfaces`
  read the drawings and compare mounting interfaces against BR-2201.

Ranked candidates come back roughly as:

| # | Part | Score | Fit | Saving |
|---|---|---|---|---|
| 1 | BR-3310 | 0.887 | exact | 1.23 kg |
| 2 | BR-5501 | 0.806 | exact | 0.90 kg |
| 3 | BR-7150 | 0.662 | **different** | 1.37 kg |
| 4 | BR-6002 | 0.192 | **different** | 0.65 kg |

Note BR-7150: it saves *more* weight than BR-5501 (1.37 kg vs 0.90 kg), so a
weight-only ranking would put it second — but its drawing shows a 4×M8 pattern
on a 150×90 mm pitch where BR-2201 needs 4×M10 on 120×80 mm. It does not bolt
in. That distinction is invisible to the coarse attribute search and is exactly
what the fine drawing analysis exists to catch.

### 2. Select a candidate

> **Let's go with BR-3310.**

### 3. Run a simulation

> **Run a stampability check on it.**

The orchestrator calls **SimulationAgent** → `run_stampability_analysis`. It fails:

```
Stampability analysis FAILED for BR-3310: 2 critical region(s), worst severity 0.71.
  R-STP-01  severity=0.71  Deep draw corner, front left radius
  R-STP-02  severity=0.44  Flange wrap, rear edge
```

### 4. Get a recommendation

> **What can we do about the worst region?**

The orchestrator summarises and calls **GeometryAgent** →
`propose_geometry_change`, which is **read-only** — it recommends, it does not
change anything:

```
increase_fillet_radius on R-STP-01
  Open up the draw radius to bring thinning back under the forming limit.
  severity 0.71 -> expected 0.39
```

### 5. The human-in-the-loop gate ⛔

> **Apply that change.**

The Geometry Agent calls `apply_modification_workflow` — and **the run pauses.**
DevUI renders an **Approve / Reject** panel showing the tool and its arguments.
Nothing has been modified yet.

- **Reject** → the change is not applied and the agent reports that plainly.
- **Approve** → the workflow runs and returns a job id and model revision.

This gate is the load-bearing governance mechanism, so it is tested directly
rather than taken on trust — see `tests/test_approval_gate.py`, which drives the
tool call with a stub model and asserts the digital twin is untouched until
approval arrives, applied on approve, and still untouched on reject.

### 6. Re-simulate and iterate

> **Re-run the stampability check.**

Severity drops. While regions remain, the loop repeats: propose → approve →
apply → re-simulate. Each cycle the orchestrator calls `record_iteration` and
then `check_loop_status`, which owns the continue/stop decision **in code** —
the model is never trusted to count iterations across turns.

### 7. Convergence

After three approved modifications the analysis passes and the loop ends:

```
Termination condition: converged
All critical regions resolved -- design converged.

Overall status for BR-3310: PASS
  stiffness      pass  criticals=0  max severity=0.284
  modal          pass  criticals=0  max severity=0.214
  stampability   pass  criticals=0  max severity=0.12
```

The orchestrator cites which of the four termination conditions fired:

| Reason | Meaning |
|---|---|
| `converged` | No critical regions remain in the loop's scope. |
| `max_iterations_reached` | Iteration cap hit (default 5) with issues still open. |
| `user_cancelled` | The engineer stopped the loop. |
| `geometry_workflow_failed` | The geometry workflow reported a failure. |

Applied modifications also improve the *other* analyses a little — stiffening a
rib shifts the modal response too — so the part reaches overall PASS. That
coupling is deliberate, and it is why the demo ends on a satisfying result
rather than a mechanical iteration cutoff.

**Other things worth trying:** run the specialists standalone (each is
registered separately in DevUI); reject an approval to see the gate hold; ask to
stop mid-loop to trigger `user_cancelled`.

---

## What's real vs. mocked

| Component | Real or mocked | Production swap-in |
|---|---|---|
| LLM calls | **Real** — live Azure AI Foundry calls via `DefaultAzureCredential` | Unchanged; point at a production deployment. |
| Agent orchestration, tool calling, approval gate, MCP transport | **Real** Agent Framework machinery | Unchanged. |
| ePLM catalog + coarse search | Mocked — `mock_data/eplm_catalog.json`, served over a real MCP server | Replace `_load_catalog()` with the ePLM query API (REST/OData). The tool signature and the agent side stay as they are. |
| Drawing analysis (fine search) | Mocked — hand-authored fixtures in `tools/drawing_analysis.py` | Call a vision-capable model over the drawing referenced by `drawingRef`, or a CAD feature-recognition service. Keep the return shape. |
| Geometry modification workflow | Mocked — `tools/geometry_tools.py` mutates an in-memory twin | Call the parametric CAD/PLM change workflow (e.g. CATIA/NX automation), returning the real job id and revision. **The approval gate stays exactly where it is.** |
| CAE analyses | Mocked — `tools/simulation_tools.py` reads the twin | Submit to the real solver (Abaqus / LS-DYNA / AutoForm) and parse results into the same unified schema. |
| Agent hosting | Local process + DevUI | Deploy these same agents to Foundry Agent Service. The repo is structured so this is a follow-on step, not a rewrite. |

### State and persistence

Loop iteration counts and the digital twin live in **module-level Python dicts**
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
mcp_servers/eplm_mcp_server.py  stdio MCP server: coarse ePLM attribute search
mock_data/
  eplm_catalog.json             Synthetic part catalog
  digital_twin.py               In-memory per-part simulation state
tools/
  drawing_analysis.py           Fine search: mounting-interface extraction
  part_search_tools.py          Ranked-candidate assembly
  simulation_tools.py           Three mocked CAE analyses
  geometry_tools.py             Propose (read-only) + apply (approval-gated)
  loop_state.py                 Deterministic iteration count and termination
agents/
  part_search/agent.py          Coarse (MCP) + fine search
  simulation/agent.py           CAE analyses
  geometry/agent.py             Carries the human-in-the-loop gate
  orchestrator/agent.py         Wires the specialists via .as_tool()
run_devui.py                    Registers all four agents in DevUI
offline_demo.py                 Walk-through mechanics without Azure
tests/                          46 tests; no credentials required
```

---

## Scope of this iteration

Assumptions made in building this — flagged rather than silently adopted:

- **Local only.** Runs and is inspected through DevUI on localhost. No
  deployment to Foundry Agent Service hosted agents.
- **In-memory state.** Resets on restart, as described above.
- **All backends synthetic.** No real ePLM, CAD, or solver is contacted.
- **Approval on applying, not proposing.** `propose_geometry_change` is
  ungated because recommending is not changing; only
  `apply_modification_workflow` mutates design state and it is always gated.
- **Sub-agent calls are ungated.** Gating the whole `as_tool()` call would ask
  the engineer to approve merely *consulting* a specialist. The gate sits on
  the one tool that changes geometry.
