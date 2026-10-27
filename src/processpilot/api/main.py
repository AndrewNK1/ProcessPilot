"""FastAPI routes over ``Workspace``, and the web app. Run with ``python -m processpilot.api``.

WHY the UI goes through HTTP instead of importing the pipeline: the agent's
review step is a paused LangGraph run that may be resumed hours later, mining
and agent runs take minutes, and both must survive a browser refresh. The
backend owns those long-lived things (jobs, checkpoints, the decision log);
the web app (``web/``, React) only displays them and sends the analyst's
choices. The same endpoints serve scripts and tests.

The built web app lives in ``api/static`` (``npm run build`` in ``web/``) and
is served from the same origin, so one process and one port run everything
and the browser needs no CORS. Its client-side routes (``/data``,
``/evidence/...``, ``/redesign/...``) all answer with ``index.html``.

Slow steps return ``{"job_id": ...}`` at once; ``GET /jobs/{id}`` reports
progress and the result. Errors: 404 unknown id, 409 wrong state (e.g. no
review pending), 422 unusable input; the body's ``detail`` says why.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
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
from processpilot.config import default_app_dir, default_runs_dir, load_config_yaml, load_env
from processpilot.pipeline import CleaningPolicy

_BPMN_MEDIA_TYPE = "application/xml"
STATIC_DIR = Path(__file__).parent / "static"
# Top-level paths owned by the web app's router; everything else is the API.
_APP_ROUTES = ("data", "evidence", "redesign")
_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>ProcessPilot</title>
<p style="font-family:sans-serif;max-width:40em;margin:4em auto">The web app is not built.
Run <code>npm install</code> and <code>npm run build</code> in <code>web/</code>, or use the
API directly at <a href="/docs">/docs</a>.</p>"""


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

    @app.get("/runs/{run_id}/segments/{slug}/variants/{rank}/waits")
    def variant_waits(run_id: str, slug: str, rank: int) -> dict[str, Any]:
        return _call(ws().variant_waits, run_id, slug, rank)

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

    # --- the web app -----------------------------------------------------------

    @app.get("/settings/ui")
    def ui_settings() -> dict[str, Any]:
        """How often the app polls a job and how tall it draws a BPMN model (app.yaml ``ui``)."""
        ui = load_config_yaml("app.yaml")["ui"]
        return {
            "poll_seconds": float(ui["poll_seconds"]),
            "bpmn_height_px": int(ui["bpmn_height_px"]),
        }

    _mount_web_app(app, STATIC_DIR)
    return app


def _mount_web_app(app: FastAPI, static_dir: Path) -> None:
    """Serve the built web app: hashed assets as files, every app route as ``index.html``."""
    index = static_dir / "index.html"
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    def page() -> Response:
        if index.is_file():
            # The shell must never be cached: a rebuild changes the asset names it points to.
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(_NOT_BUILT, status_code=503)

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon() -> Response:
        icon = static_dir / "favicon.svg"
        if not icon.is_file():
            raise HTTPException(404, "no favicon")
        return FileResponse(icon, media_type="image/svg+xml")

    app.add_api_route("/", page, include_in_schema=False)
    for route in _APP_ROUTES:
        app.add_api_route(f"/{route}", page, include_in_schema=False)
        app.add_api_route(f"/{route}/{{rest:path}}", page, include_in_schema=False)
