"""HTTP client for the ProcessPilot API: the only way the UI reaches the pipeline.

WHY the UI never imports the pipeline: everything it shows must be exactly what
the backend stored (evidence, proposals, decisions), and the review step must
go through the same endpoint a script or test would use. Errors come back as
``ApiError`` carrying the API's own explanation (``detail``).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from processpilot.config import load_config_yaml

_API_URL_ENV = "PP_API_URL"


class ApiError(Exception):
    """A non-2xx answer (or no answer) from the API, with its explanation."""

    def __init__(self, status: int | None, detail: str) -> None:
        """``status`` is ``None`` when the API could not be reached."""
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}" if status else detail)


def ui_settings() -> dict[str, Any]:
    """The ``ui`` block of ``configs/app.yaml``, with ``PP_API_URL`` applied."""
    settings = dict(load_config_yaml("app.yaml")["ui"])
    settings["api_url"] = os.environ.get(_API_URL_ENV) or settings["api_url"]
    return settings


class ApiClient:
    """Thin, typed-by-convention wrapper over the routes in ``api/main.py``."""

    def __init__(self, base_url: str, timeout: float) -> None:
        """One pooled HTTP connection for the session."""
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(None, f"cannot reach the API at {self.base_url} ({exc})") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, str(detail))
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, path, **kwargs).json()

    # status
    def health(self) -> dict[str, Any]:
        """Model, Graphviz and job status."""
        return self._json("GET", "/health")

    def presets(self) -> list[dict[str, Any]]:
        """Dataset presets and whether their source is reachable."""
        return self._json("GET", "/presets")

    # datasets
    def datasets(self) -> list[dict[str, Any]]:
        """All datasets, newest first."""
        return self._json("GET", "/datasets")

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        """A dataset with its profile, quality report and suggested cleaning."""
        return self._json("GET", f"/datasets/{dataset_id}")

    def create_preset_dataset(self, preset: str) -> dict[str, Any]:
        """Use a configured preset's source and mapping."""
        return self._json("POST", "/datasets/preset", json={"preset": preset})

    def upload_dataset(self, filename: str, data: bytes, base_preset: str) -> dict[str, Any]:
        """Upload an event log file."""
        return self._json(
            "POST",
            "/datasets/upload",
            files={"file": (filename, data)},
            data={"base_preset": base_preset},
        )

    def profile_dataset(self, dataset_id: str) -> str:
        """Start profiling; returns the job id."""
        return self._json("POST", f"/datasets/{dataset_id}/profile")["job_id"]

    def set_mapping(self, dataset_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        """Confirm the column mapping."""
        return self._json("PUT", f"/datasets/{dataset_id}/mapping", json=mapping)

    def validate_dataset(self, dataset_id: str) -> str:
        """Start the data-quality check; returns the job id."""
        return self._json("POST", f"/datasets/{dataset_id}/validate")["job_id"]

    def mine_dataset(self, dataset_id: str, policy: dict[str, bool]) -> tuple[str, str]:
        """Start mining; returns (job id, run id)."""
        body = self._json("POST", f"/datasets/{dataset_id}/runs", json=policy)
        return body["job_id"], body["run_id"]

    # runs
    def runs(self) -> list[dict[str, Any]]:
        """All runs, newest first."""
        return self._json("GET", "/runs")

    def run(self, run_id: str) -> dict[str, Any]:
        """Run record, manifest, segments and data quality."""
        return self._json("GET", f"/runs/{run_id}")

    def set_run_preset(self, run_id: str, preset: str) -> dict[str, Any]:
        """Name the preset of a run found on disk."""
        return self._json("PUT", f"/runs/{run_id}/preset", json={"preset": preset})

    def evidence(self, run_id: str, slug: str, type_: str | None = None) -> list[dict[str, Any]]:
        """A segment's evidence items, optionally of one type."""
        params = {"type": type_} if type_ else None
        return self._json("GET", f"/runs/{run_id}/segments/{slug}/evidence", params=params)

    def segment_model(self, run_id: str, slug: str) -> dict[str, Any]:
        """Model activities and default constraints."""
        return self._json("GET", f"/runs/{run_id}/segments/{slug}/model")

    def as_is_bpmn(self, run_id: str, slug: str) -> str:
        """As-is BPMN XML."""
        return self._request("GET", f"/runs/{run_id}/segments/{slug}/as-is.bpmn").text

    # agent
    def agent_runs(self, run_id: str, slug: str) -> list[dict[str, Any]]:
        """Agent runs of a segment, newest first."""
        return self._json("GET", f"/runs/{run_id}/segments/{slug}/agent-runs")

    def start_agent(
        self,
        run_id: str,
        slug: str,
        constraints: dict[str, Any] | None,
        model_id: str | None,
    ) -> str:
        """Start an agent run; returns the job id."""
        body = {"constraints": constraints, "model_id": model_id or None}
        return self._json("POST", f"/runs/{run_id}/segments/{slug}/agent-runs", json=body)["job_id"]

    def agent_run(self, run_id: str, slug: str, agent_run_id: str) -> dict[str, Any]:
        """State, proposals, pending review and decisions of one agent run."""
        return self._json("GET", f"/runs/{run_id}/segments/{slug}/agent-runs/{agent_run_id}")

    def to_be_bpmn(self, run_id: str, slug: str, agent_run_id: str, index: int) -> str:
        """To-be BPMN XML of one proposal."""
        path = f"/runs/{run_id}/segments/{slug}/agent-runs/{agent_run_id}/proposals/{index}"
        return self._request("GET", f"{path}/to-be.bpmn").text

    def decide(
        self,
        run_id: str,
        slug: str,
        agent_run_id: str,
        decision: str,
        comment: str | None,
        analyst: str | None,
    ) -> str:
        """Send the analyst's decision; returns the resume job id."""
        body = {"decision": decision, "comment": comment, "analyst": analyst}
        path = f"/runs/{run_id}/segments/{slug}/agent-runs/{agent_run_id}/decision"
        return self._json("POST", path, json=body)["job_id"]

    # jobs
    def job(self, job_id: str) -> dict[str, Any]:
        """One job's status, progress line, error and result."""
        return self._json("GET", f"/jobs/{job_id}")

    def jobs(self, active: bool = False) -> list[dict[str, Any]]:
        """Jobs, newest first."""
        return self._json("GET", "/jobs", params={"active": active})
