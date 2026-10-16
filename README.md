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

Project rules live in [CLAUDE.md](CLAUDE.md).
