r"""Mine the frozen evaluation run (no LLM; full BPI 2019 takes about 3 minutes).

Usage (PowerShell, from the project root):

    .\.venv\python.exe scripts\eval\mine_frozen_run.py --preset bpi2019

Refuses uncommitted changes in src/ or configs/ and any preset other than the
one in configs/eval.yaml. The run is registered in the app's SQLite index as
purpose 'evaluation' with its preset, and the app refuses agent runs on it.
Prints the run id.
"""

from __future__ import annotations

import argparse
import time

from processpilot.api.db import Database
from processpilot.api.service import DB_FILE
from processpilot.config import default_app_dir, default_runs_dir, find_project_root, load_env
from processpilot.evaluation.workflow import mine_frozen_run


def main() -> None:
    """Parse arguments and mine."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--preset", required=True, help="dataset preset (must match eval.yaml)")
    args = parser.parse_args()
    load_env()
    started = time.perf_counter()
    db = Database(default_app_dir() / DB_FILE)
    run_id = mine_frozen_run(find_project_root(), args.preset, default_runs_dir(), db)
    print(f"frozen evaluation run: {run_id} ({time.perf_counter() - started:.0f} s)")


if __name__ == "__main__":
    main()
