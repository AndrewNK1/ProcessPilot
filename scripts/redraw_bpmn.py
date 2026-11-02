r"""Redraw the BPMN files of existing runs with the current renderer.

WHY: the app serves the ``.bpmn`` files a run wrote, so a better drawing only
reaches runs made after it, and re-mining would mint a new run id and lose the
agent runs and decisions attached to the old one. The process trees are on
disk, so the drawings can be rebuilt without touching evidence or event data.

A to-be model is rebuilt by replaying its proposal's edits on the as-is tree.
Each new drawing goes to a temporary file first and replaces the saved one only
if both show the same tasks, so a replay that gave a different model is reported
and its file left alone.

Run (PowerShell):
    .\.venv\python.exe scripts\redraw_bpmn.py
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from processpilot.agent.state import RedesignSet
from processpilot.config import find_project_root
from processpilot.process_tree import TreeNode, render_png, write_bpmn
from processpilot.redesign.bpmn_edit import apply_proposal, load_tree, write_to_be
from processpilot.redesign.operations import EditError

TASK = re.compile(r'<bpmn:task\b[^>]*\bid="([^"]+)"[^>]*\bname="([^"]*)"')


def tasks_of(path: Path) -> set[tuple[str, str]]:
    """The ``(id, name)`` of every task in a BPMN file."""
    return set(TASK.findall(path.read_text(encoding="utf-8")))


def same_tasks(tree: TreeNode, saved: Path) -> bool:
    """Whether a tree's drawing would show exactly the tasks of the saved file."""
    with tempfile.TemporaryDirectory() as tmp:
        trial = Path(tmp) / "trial.bpmn"
        write_bpmn(tree, trial)
        return tasks_of(trial) == tasks_of(saved)


def redraw_agent_run(as_is: TreeNode, directory: Path) -> tuple[int, list[str]]:
    """Redraw one agent run's to-be models; returns the count and any refusals."""
    redrawn, refused = 0, []
    for log in sorted(directory.glob("proposals_r*.json")):
        revision = log.stem.removeprefix("proposals_r")
        proposals = RedesignSet.model_validate(json.loads(log.read_text(encoding="utf-8")))
        for i, proposal in enumerate(proposals.proposals):
            stem = f"to_be_r{revision}_p{i + 1}"
            saved = directory / f"{stem}.bpmn"
            if not saved.is_file():
                continue
            try:
                to_be, _ = apply_proposal(as_is, proposal.edit_operations)
            except EditError as exc:
                refused.append(f"{saved}: no longer applies ({exc})")
                continue
            if not same_tasks(to_be, saved):
                refused.append(f"{saved}: replay gives different tasks")
                continue
            write_to_be(to_be, directory, stem)
            redrawn += 1
    return redrawn, refused


def redraw_runs(runs_dir: Path) -> tuple[int, int, list[str]]:
    """Redraw every run below ``runs_dir``; returns as-is count, to-be count, refusals."""
    as_is_count, to_be_count, refused = 0, 0, []
    for tree_file in sorted(runs_dir.glob("*/*/as_is_tree.json")):
        segment_dir = tree_file.parent
        saved = segment_dir / "as_is.bpmn"
        tree = load_tree(tree_file)
        if saved.is_file() and not same_tasks(tree, saved):
            refused.append(f"{saved}: tree and drawing show different tasks")
        else:
            write_bpmn(tree, saved, process_id=f"as_is_{segment_dir.name}")
            render_png(tree, segment_dir / "as_is.png")
            as_is_count += 1
        for agent_dir in sorted((segment_dir / "agent").glob("*")):
            done, problems = redraw_agent_run(tree, agent_dir)
            to_be_count += done
            refused.extend(problems)
    return as_is_count, to_be_count, refused


def main() -> None:
    """Redraw all runs and print what happened."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, default=find_project_root() / "outputs" / "runs")
    args = parser.parse_args()
    as_is_count, to_be_count, refused = redraw_runs(args.runs)
    print(f"redrew {as_is_count} as-is and {to_be_count} to-be models; left {len(refused)} alone")
    for line in refused:
        print(f"  {line}")


if __name__ == "__main__":
    main()
