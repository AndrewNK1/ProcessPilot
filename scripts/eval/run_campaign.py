r"""Run an evaluation campaign: every run in order, within the Gemini quota.

Usage (PowerShell, from the project root):

    .\.venv\python.exe scripts\eval\run_campaign.py --campaign eval-2026-11-v1 --status
    .\.venv\python.exe scripts\eval\run_campaign.py --campaign eval-2026-11-v1 --dry-run
    .\.venv\python.exe scripts\eval\run_campaign.py --campaign eval-2026-11-v1
    .\.venv\python.exe scripts\eval\run_campaign.py --campaign eval-2026-11-v1 `
        --smoke "2-way match"

Without --dry-run it needs a sealed campaign and makes real model calls (only
when started by hand). --dry-run runs the whole grid with the scripted model
(zero API calls) in <campaign>\dry-run\ with a simulated clock. --status reads
SQLite and the ledger only. --smoke makes one real dev run of one segment.
Stops when today's budget cannot cover the next run, prints the next reset in
Cairo time; start it again the next day.
"""

from __future__ import annotations

import argparse
import sys

from processpilot.agent.ratelimit import QuotaStopped
from processpilot.api.db import Database
from processpilot.api.service import DB_FILE
from processpilot.config import default_app_dir, default_eval_dir, find_project_root, load_env
from processpilot.evaluation.launch import dry_runner, real_runner, smoke_run


def main() -> int:
    """Parse arguments and run; exit code 0 = finished or paused normally."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--status", action="store_true", help="print progress; no calls")
    parser.add_argument("--dry-run", action="store_true", help="scripted model, no API calls")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--only-condition", choices=["processpilot", "baseline"])
    parser.add_argument("--smoke", metavar="SEGMENT", help="one real dev run of SEGMENT")
    parser.add_argument(
        "--accept-served-model",
        metavar="MODEL",
        help="record that the campaign continues with this served model",
    )
    args = parser.parse_args()
    load_env()
    root = find_project_root()
    campaign_dir = default_eval_dir() / args.campaign
    db = Database(default_app_dir() / DB_FILE)

    if args.smoke:
        try:
            attempt = smoke_run(root, campaign_dir, db, args.smoke)
        except QuotaStopped as exc:
            print(f"not started: {exc}")
            return 1
        print(
            f"smoke run {attempt.agent_run_id}: {attempt.outcome} "
            f"({attempt.requests_sent} requests, {attempt.llm_calls} calls)"
        )
        return 0

    runner = (dry_runner if args.dry_run else real_runner)(root, campaign_dir, db)
    if args.accept_served_model:
        runner.accept_served_model(args.accept_served_model, "accepted on the command line")
        print(f"served model {args.accept_served_model} accepted")
        return 0
    if args.status:
        print(runner.status())
        return 0
    report = runner.run(args.max_runs, args.only_condition)
    print(f"stopped: {report.stopped_because} ({len(report.executed)} runs this session)")
    print(runner.status())
    return 0


if __name__ == "__main__":
    sys.exit(main())
