"""Applies a proposal's edit operations to the as-is model and writes ``to_be.bpmn``.

WHY it fails loudly: a partially applied edit would yield a to-be model that
does not correspond to the proposal the analyst approved. Any invalid or
unapplicable operation raises ``EditError``; there is no best-effort mode.

Also an enforcement point for ``protected_activities``: the constraint is in
the redesign prompt and checked by the agent's validator, but a prompt cannot
be relied upon, so ``operations.apply_operation`` checks it again here.

The as-is model is the process tree mining wrote (``as_is_tree.json``); the
agent layer reads that file, never event data.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from processpilot.mining.run import AS_IS_TREE_FILE, CASE_WAITS_FILE  # noqa: F401 -- re-export
from processpilot.process_tree import TreeNode, render_png, write_bpmn
from processpilot.redesign.operations import (
    EditOperation,
    ModelDiff,
    apply_operations,
    diff_trees,
)
from processpilot.redesign.simulate import EstimateSettings


class RedesignSettings(BaseModel):
    """``redesign`` block of ``configs/agent.yaml``."""

    estimate: EstimateSettings
    woflan_max_transitions: int = Field(gt=0)


def load_tree(path: Path) -> TreeNode:
    """Read a tree written by ``save_tree``."""
    return TreeNode.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def save_tree(tree: TreeNode, path: Path) -> None:
    """Write a tree as indented JSON (deterministic for identical trees)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tree.model_dump_json(indent=2), encoding="utf-8")


def apply_proposal(
    as_is: TreeNode, ops: list[EditOperation], protected: frozenset[str] = frozenset()
) -> tuple[TreeNode, ModelDiff]:
    """The to-be tree and its diff; ``EditError`` if any operation cannot be applied."""
    to_be = apply_operations(as_is, ops, protected)
    return to_be, diff_trees(as_is, to_be, ops)


def write_to_be(tree: TreeNode, directory: Path, stem: str) -> tuple[Path, Path | None]:
    """Write ``<stem>.bpmn`` (and ``<stem>.png`` when Graphviz is available)."""
    bpmn_path = directory / f"{stem}.bpmn"
    write_bpmn(tree, bpmn_path, process_id=stem)
    png_path = directory / f"{stem}.png"
    return bpmn_path, png_path if render_png(tree, png_path) else None
