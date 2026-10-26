"""``python -m processpilot.api``: serve the API on the host/port in ``configs/app.yaml``."""

from __future__ import annotations

import uvicorn

from processpilot.api.main import create_app
from processpilot.api.service import AppSettings
from processpilot.config import load_env


def main() -> None:
    """Start uvicorn (one process; jobs run in its thread pool)."""
    load_env()
    settings = AppSettings.load()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
