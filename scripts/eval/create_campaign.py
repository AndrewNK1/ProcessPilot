r"""Draft or seal an evaluation campaign manifest (no LLM).

Usage (PowerShell, from the project root):

    # now: after build_answer_key.py
    .\.venv\python.exe scripts\eval\create_campaign.py `
        --campaign eval-2026-11-v1 --mining-run <run_id>
    # after Phases 5-6 are committed:
    .\.venv\python.exe scripts\eval\create_campaign.py --campaign eval-2026-11-v1 --seal

Drafting records the frozen run's file hashes, the source log's hash, the
preset hash and the answer key's hash. Sealing re-checks all of them, requires
a clean src/ and configs/, records the environment (model id from PP_MODEL
unless --model-id is given) and creates the git tag (default eval-v1).
"""

from __future__ import annotations

import argparse
import os
import subprocess

from processpilot.api.db import Database
from processpilot.api.service import DB_FILE
from processpilot.config import (
    default_app_dir,
    default_eval_dir,
    default_runs_dir,
    find_project_root,
    load_env,
)
from processpilot.evaluation.workflow import create_draft, seal


def main() -> None:
    """Parse arguments, then draft or seal."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--mining-run", help="frozen run id (drafting)")
    parser.add_argument("--seal", action="store_true", help="seal an existing draft")
    parser.add_argument("--model-id", help="requested model id (sealing; default PP_MODEL)")
    parser.add_argument("--tag", default="eval-v1", help="git tag created on sealing")
    args = parser.parse_args()
    load_env()
    root = find_project_root()
    campaign_dir = default_eval_dir() / args.campaign
    db = Database(default_app_dir() / DB_FILE)
    if args.seal:
        model_id = args.model_id or os.environ.get("PP_MODEL", "").strip()
        if not model_id:
            raise SystemExit("no model id: set PP_MODEL in .env or pass --model-id")
        sealed = seal(root, campaign_dir, model_id, db, args.tag)
        subprocess.run(
            ["git", "tag", args.tag, sealed.environment.git_commit], cwd=root, check=True
        )
        print(f"sealed {args.campaign} at {sealed.environment.git_commit}, tag {args.tag}")
        return
    if not args.mining_run:
        raise SystemExit("--mining-run is required to draft a campaign")
    manifest = create_draft(
        root, campaign_dir, args.campaign, default_runs_dir() / args.mining_run, db
    )
    print(
        f"draft campaign {manifest.campaign_id}: run {manifest.frozen.mining_run_id}, "
        f"{len(manifest.frozen.files)} frozen files, "
        f"answer key {manifest.answer_key.sha256[:12]}..."
    )


if __name__ == "__main__":
    main()
