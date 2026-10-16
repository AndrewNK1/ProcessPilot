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

# 2. Install, editable, with dev + UI extras
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ui]"

# 3. Point the tool at your data and model
Copy-Item .env.example .env   # then edit .env
```

`.env` is gitignored. The raw event log stays outside the repository; only its
path is configured, via `PP_BPI2019_PATH`.

## Checks

```powershell
ruff check .
pytest
```

## Layout

| Path | Role |
| --- | --- |
| `src/processpilot/sources/` | `DataSource` implementations (file, SQL, Odoo) |
| `src/processpilot/ingest/` | column mapping + data-quality validation |
| `src/processpilot/mining/` | deterministic analysis + evidence store |
| `src/processpilot/agent/` | LangGraph pipeline (reasoning only) |
| `src/processpilot/redesign/` | typed edit ops, BPMN editing, impact estimate |
| `configs/` | dataset presets and the redesign heuristics catalogue |
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
purchase-to-pay log (1,595,923 events / 251,734 cases). The raw file is **not**
in this repository — point `PP_BPI2019_PATH` at your own copy.

## License note

pm4py (community edition) is AGPL v3, which has implications for any
derivative distribution of this project.
