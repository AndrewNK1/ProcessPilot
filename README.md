# ProcessPilot

An evidence-grounded agentic AI consultant for the **Analysis → Redesign**
phases of the BPM lifecycle. Bachelor's thesis project, Business Informatics.

ProcessPilot ingests event data, mines the as-is process, identifies
bottlenecks with traceable evidence, and proposes redesigned processes (to-be
BPMN) with rationale. **It never executes changes** — a human analyst approves
or rejects every proposal.

The design separation that makes it auditable: **pandas/pm4py compute every
number, the LLM only interprets precomputed evidence.** Every agent claim cites
a stable evidence ID; claims citing unknown IDs are rejected and retried.

## Setup (PowerShell)

```powershell
# 1. Create the Python 3.11 environment (see Phase 0 notes for interpreter choice)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install, editable, with the dev extras (the web app is already built; no Node needed)
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 3. Point the tool at your data and model
Copy-Item .env.example .env   # then edit .env

# 4. Graphviz (BPMN layout and pictures), a system program, not a pip package
winget install --id Graphviz.Graphviz   # then add C:\Program Files\Graphviz\bin to PATH
```

`.env` is gitignored. Set `PP_MODEL` to `anthropic:claude-opus-5-5` (with
`ANTHROPIC_API_KEY`) for the real model, or to `mock` to run everything without
a key (rule-based stand-in, every output marked `[mock]`). Without Graphviz the
pipeline still runs, but BPMN diagrams have no layout.

## Run the app

```powershell
.\scripts\run_app.ps1
```

This starts one process, the FastAPI backend, which serves both the API
(interactive docs at `/docs`) and the web app at `http://127.0.0.1:8000`, then
opens the browser. Log: `outputs\app\api.log`. Ctrl+C stops it. Without the
script: `.\.venv\python.exe -m processpilot.api`, then open the address.

A first run in under a minute: page **Data** -> preset `bpi2019_sample` ->
*Check data quality* -> *Mine this log*; page **Evidence**; page **Redesign** ->
*Run the agent* (model `mock` works without a key) -> approve or reject with a
comment.

Light and dark mode: the switch at the foot of the sidebar (System follows
the operating system). The look is documented in `DESIGN.md`; its tokens live
in `web\src\styles\tokens.css`. Every screen has its own address (for example
`/evidence/<run>/<segment>?path=3`), so a view can be bookmarked or shared.

The web app only talks to the API. The API keeps a SQLite index
(`outputs\app\processpilot.sqlite`) of datasets, runs, background jobs and
agent runs, and the log of every analyst decision; evidence, models and agent
logs stay in the run folders under `outputs\runs`. Runs mined in notebooks show
up in the app automatically.

## Checks

```powershell
ruff check .
pytest
# web app (needs Node 22+): type check and unit tests
cd web; npm ci; npm run check; npm test
```

## Changing the web app

The front end is React + TypeScript, built with Vite, in `web/` (see
`web\README.md`). `npm run dev` serves it with hot reload on
`http://localhost:5173` and forwards API calls to a running backend.
`npm run build` writes the production build into
`src\processpilot\api\static`, which is committed, so the app runs with Python
alone; rebuild and commit it after every UI change.

## Layout

| Path | Role |
| --- | --- |
| `src/processpilot/sources/` | `DataSource` implementations (file, SQL, Odoo) |
| `src/processpilot/ingest/` | column mapping + data-quality validation |
| `src/processpilot/mining/` | deterministic analysis + evidence store |
| `src/processpilot/agent/` | LangGraph pipeline (reasoning only) |
| `src/processpilot/redesign/` | typed edit ops, BPMN editing, impact estimate |
| `src/processpilot/process_tree.py` | editable process tree, BPMN rendering with stable ids |
| `src/processpilot/pipeline.py` | ingest -> validate -> clean -> mine, shared by API and notebooks |
| `src/processpilot/llm/` | rule-based mock model (`PP_MODEL=mock`) |
| `src/processpilot/api/` | FastAPI backend, background jobs, SQLite index + decision log; serves the built web app (`api/static`) |
| `web/` | web app source: React + TypeScript + Vite, bpmn-js viewer (calls the API only) |
| `configs/` | dataset presets, heuristics catalogue, agent and app settings |
| `data/sample/` | committed 800-case BPI 2019 sample (preset `bpi2019_sample`) |
| `scripts/` | `run_app.ps1` (start the app), `make_bpi_sample.py` |
| `notebooks/` | per-phase evaluation notebooks |

## Design principles

1. **Deterministic computation, LLM reasoning.** pm4py/pandas compute every
   number; the LLM only interprets precomputed evidence. It never sees raw
   event data and never calculates a metric.
2. **Evidence-grounded.** Every finding carries a stable evidence ID. Agent
   claims must cite them, and outputs citing unknown IDs are rejected and
   retried.
3. **Fixed pipeline.** An explicit LangGraph graph with typed (Pydantic)
   structured outputs — not a free-roaming agent.
4. **Source-agnostic.** File, SQL and ERP inputs all pass through one
   `DataSource` interface into a single canonical schema.
5. **Segment-aware.** The pipeline runs per segment (configurable via
   `segment_by`), since e.g. each BPI 2019 `Item Category` is a materially
   different purchase-to-pay flow.
6. **Human in the loop.** The final step is a LangGraph `interrupt()` for
   analyst approval. The system never executes a change.
7. **Reproducible.** Model id, prompts and library versions are logged per run
   to `outputs/runs/<run_id>/`; `requirements.lock` pins the verified
   dependency set.

## Dataset

Evaluated on the [BPI Challenge 2019](https://data.4tu.nl/articles/_/12715853)
purchase-to-pay log (1,595,923 events / 251,734 cases). The pipeline reads the
XES from the path in `PP_BPI2019_PATH`. For convenience the original download,
`BPI_Challenge_2019.zip`, is committed (unzip it outside the repo and point the
variable at the `.xes`). `data/sample/` holds a deterministic 800-case sample
(200 per item category) that mines in seconds; see `data/sample/README.md`.

## License note

pm4py (community edition) is AGPL v3, which has implications for any
derivative distribution of this project.
