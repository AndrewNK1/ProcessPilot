"""FastAPI routes over ``Workspace``. Run with ``python -m processpilot.api``.

WHY the UI goes through HTTP instead of importing the pipeline: the agent's
review step is a paused LangGraph run that may be resumed hours later, mining
and agent runs take minutes, and both must survive a browser refresh. The
backend owns those long-lived things (jobs, checkpoints, the decision log);
Streamlit only displays them and sends the analyst's choices. The same
endpoints serve scripts and tests.

Slow steps return ``{"job_id": ...}`` at once; ``GET /jobs/{id}`` reports
progress and the result. Errors: 404 unknown id, 409 wrong state (e.g. no
review pending), 422 unusable input; the body's ``detail`` says why.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from processpilot.agent.state import Constraints, ReviewDecision
from processpilot.api.service import (
    AppSettings,
    Conflict,
    Invalid,
    NotFound,
    Workspace,
    parse_mapping,
)
from processpilot.config import default_app_dir, default_runs_dir, load_env
from processpilot.pipeline import CleaningPolicy

_BPMN_MEDIA_TYPE = "application/xml"


class PresetDatasetRequest(BaseModel):
    """Start from a configured preset (its source file and mapping)."""

    preset: str


class RunPresetRequest(BaseModel):
    """Name the preset a run found on disk was mined with."""

    preset: str


class AgentStartRequest(BaseModel):
    """Optional overrides for an agent run; defaults come from the preset and ``PP_MODEL``."""

    constraints: Constraints | None = None
    model_id: str | None = None


class DecisionRequest(BaseModel):
    """The analyst's review decision, as LangGraph's resume payload plus who made it."""

    decision: Literal["approve", "reject"]
    comment: str | None = None
    analyst: str | None = None


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a service call, translating its exceptions into HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except Invalid as exc:
        raise HTTPException(422, str(exc)) from exc


def create_app(workspace: Workspace | None = None) -> FastAPI:
    """The API; without a workspace, one over the default folders is opened at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # pm4py 2.7 looks up its parent process's name (psutil) when first
        # imported and raises NoSuchProcess if that parent has exited, which
        # breaks every later import in the process. A server outlives its
        # launcher, so import now, while the launcher still waits for /health,
        # instead of lazily in the first mining job.
        import pm4py  # noqa: F401

        if workspace is None:
            load_env()
            app.state.ws = Workspace(default_runs_dir(), default_app_dir(), AppSettings.load())
        else:
            app.state.ws = workspace
        yield
        if workspace is None:
            app.state.ws.close()

    app = FastAPI(
        title="ProcessPilot API",
        description="Evidence-grounded process analysis and redesign; a human decides.",
        lifespan=lifespan,
    )

    def ws() -> Workspace:
        return app.state.ws

    # --- status --------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, Any]:
        return ws().health()

    @app.get("/presets")
    def presets() -> list[dict[str, Any]]:
        return ws().presets()

    # --- datasets ------------------------------------------------------------

    @app.get("/datasets")
    def datasets() -> list[dict[str, Any]]:
        return ws().datasets()

    @app.post("/datasets/preset", status_code=201)
    def create_preset_dataset(body: PresetDatasetRequest) -> dict[str, Any]:
        return _call(ws().create_preset_dataset, body.preset)

    @app.post("/datasets/upload", status_code=201)
    def upload_dataset(
        file: Annotated[UploadFile, File()], base_preset: Annotated[str, Form()] = "generic"
    ) -> dict[str, Any]:
        return _call(ws().create_upload_dataset, file.filename or "upload", file.file, base_preset)

    @app.get("/datasets/{dataset_id}")
    def dataset(dataset_id: str) -> dict[str, Any]:
        return _call(ws().dataset, dataset_id)

    @app.post("/datasets/{dataset_id}/profile", status_code=202)
    def profile_dataset(dataset_id: str) -> dict[str, str]:
        return {"job_id": _call(ws().profile_dataset, dataset_id)}

    @app.put("/datasets/{dataset_id}/mapping")
    def set_mapping(dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return _call(lambda: ws().set_mapping(dataset_id, parse_mapping(body)))

    @app.post("/datasets/{dataset_id}/validate", status_code=202)
    def validate_dataset(dataset_id: str) -> dict[str, str]:
        return {"job_id": _call(ws().validate_dataset, dataset_id)}

    @app.post("/datasets/{dataset_id}/runs", status_code=202)
    def mine_dataset(dataset_id: str, policy: CleaningPolicy) -> dict[str, str]:
        job_id, run_id = _call(ws().mine_dataset, dataset_id, policy)
        return {"job_id": job_id, "run_id": run_id}

    # --- runs and evidence ---------------------------------------------------

    @app.get("/runs")
    def runs() -> list[dict[str, Any]]:
        return ws().runs()

    @app.get("/runs/{run_id}")
    def run(run_id: str) -> dict[str, Any]:
        return _call(ws().run, run_id)

    @app.put("/runs/{run_id}/preset")
    def set_run_preset(run_id: str, body: RunPresetRequest) -> dict[str, Any]:
        return _call(ws().set_run_preset, run_id, body.preset)

    @app.get("/runs/{run_id}/segments/{slug}/evidence")
    def evidence(run_id: str, slug: str, type: str | None = Query(None)) -> list[dict[str, Any]]:
        return _call(ws().evidence, run_id, slug, type)

    @app.get("/runs/{run_id}/segments/{slug}/model")
    def segment_model(run_id: str, slug: str) -> dict[str, Any]:
        return _call(ws().segment_model, run_id, slug)

    @app.get("/runs/{run_id}/segments/{slug}/as-is.bpmn")
    def as_is_bpmn(run_id: str, slug: str) -> Response:
        return Response(_call(ws().as_is_bpmn, run_id, slug), media_type=_BPMN_MEDIA_TYPE)

    # --- agent ---------------------------------------------------------------

    @app.get("/runs/{run_id}/segments/{slug}/agent-runs")
    def agent_runs(run_id: str, slug: str) -> list[dict[str, Any]]:
        return _call(ws().agent_runs, run_id, slug)

    @app.post("/runs/{run_id}/segments/{slug}/agent-runs", status_code=202)
    def start_agent(run_id: str, slug: str, body: AgentStartRequest) -> dict[str, str]:
        return {"job_id": _call(ws().start_agent, run_id, slug, body.constraints, body.model_id)}

    @app.get("/runs/{run_id}/segments/{slug}/agent-runs/{agent_run_id}")
    def agent_run(run_id: str, slug: str, agent_run_id: str) -> dict[str, Any]:
        return _call(ws().agent_run, run_id, slug, agent_run_id)

    @app.get(
        "/runs/{run_id}/segments/{slug}/agent-runs/{agent_run_id}/proposals/{index}/to-be.bpmn"
    )
    def to_be_bpmn(run_id: str, slug: str, agent_run_id: str, index: int) -> Response:
        xml = _call(ws().to_be_bpmn, run_id, slug, agent_run_id, index)
        return Response(xml, media_type=_BPMN_MEDIA_TYPE)

    @app.post("/runs/{run_id}/segments/{slug}/agent-runs/{agent_run_id}/decision", status_code=202)
    def decide(run_id: str, slug: str, agent_run_id: str, body: DecisionRequest) -> dict[str, str]:
        decision = ReviewDecision(decision=body.decision, comment=body.comment)
        return {"job_id": _call(ws().decide, run_id, slug, agent_run_id, decision, body.analyst)}

    # --- jobs and the decision log -------------------------------------------

    @app.get("/jobs")
    def jobs(active: bool = False) -> list[dict[str, Any]]:
        return ws().all_jobs(active)

    @app.get("/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return _call(ws().job, job_id)

    @app.get("/decisions")
    def decisions(run_id: str | None = None) -> list[dict[str, Any]]:
        return ws().all_decisions(run_id)

    return app
