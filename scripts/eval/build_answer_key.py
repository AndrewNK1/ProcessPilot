r"""Write the RQ1 answer key of a campaign from the frozen mining run (no LLM).

Usage (PowerShell, from the project root):

    .\.venv\python.exe scripts\eval\build_answer_key.py `
        --campaign eval-2026-11-v1 --mining-run <run_id>

Writes outputs/eval/<campaign>/answer_key.json and .csv once (read-only; an
existing key is never replaced) and prints its SHA-256. Then draft the
campaign with create_campaign.py.
"""

from __future__ import annotations

import argparse

from processpilot.config import default_eval_dir, default_runs_dir, load_env
from processpilot.evaluation.workflow import build_key


def main() -> None:
    """Parse arguments and build the key."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--mining-run", required=True)
    args = parser.parse_args()
    load_env()
    run_dir = default_runs_dir() / args.mining_run
    if not (run_dir / "manifest.json").is_file():
        raise SystemExit(f"no mined run {args.mining_run} in {default_runs_dir()}")
    path, digest = build_key(default_eval_dir() / args.campaign, args.campaign, run_dir)
    print(f"answer key: {path}\nsha256: {digest}")


if __name__ == "__main__":
    main()
