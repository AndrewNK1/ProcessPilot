"""Typed edit operations and their application to a process tree.

WHY a closed set of operations rather than free-text recommendations: a
proposal expressed as typed operations can be applied, checked for soundness
and estimated. Free text can only be read. The models here are the exact
vocabulary the redesign step of the agent must speak (``agent/state.py``
re-exports them); the functions are the only thing that executes them.

Semantics on the as-is process tree (every activity occurs once in it):

- ``remove_activity``: the activity becomes a silent step.
- ``add_activity`` / ``add_check``: a new activity (a check is drawn and
  reported as one) directly after an existing one.
- ``reorder_activities``: the activity is removed from its place and inserted
  directly after ``after``; it then occurs exactly when ``after`` does.
- ``parallelize_activities`` / ``merge_activities``: the activities must be
  adjacent steps of one sequence (no other step between them, none of them
  inside a choice, loop or parallel block); they become a parallel block, or
  one activity named ``merged_name``.
- ``automate_activity``: the activity stays where it is, marked automated.

Any operation that cannot be applied raises ``EditError`` with a reason the
redesign step can act on; there is no best-effort mode, because a partially
applied proposal would not be the proposal the analyst approved. Protected
activities may not be removed, moved, merged or parallelized; they may be
automated and may anchor ``add_*``/``reorder`` (user decisions, Phases 3-4).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from processpilot.process_tree import (
    TreeNode,
    assert_unique_labels,
    labels,
    leaf,
    leaves,
    node,
    normalize,
    to_text,
)

OperationName = Literal[
    "remove_activity",
    "parallelize_activities",
    "reorder_activities",
    "add_activity",
    "merge_activities",
    "add_check",
    "automate_activity",
]
OPERATION_NAMES: tuple[str, ...] = OperationName.__args__  # type: ignore[attr-defined]


class RemoveActivity(BaseModel):
    """Delete ``activity``; its predecessors connect to its successors."""

    op: Literal["remove_activity"] = "remove_activity"
    activity: str


class ParallelizeActivities(BaseModel):
    """Run ``activities`` (adjacent steps of one sequence) concurrently."""

    op: Literal["parallelize_activities"] = "parallelize_activities"
    activities: list[str] = Field(min_length=2)


class ReorderActivities(BaseModel):
    """Move ``activity`` so that it directly follows ``after``."""

    op: Literal["reorder_activities"] = "reorder_activities"
    activity: str
    after: str


class AddActivity(BaseModel):
    """Insert a new activity ``name`` directly after the existing ``after``."""

    op: Literal["add_activity"] = "add_activity"
    name: str
    after: str


class MergeActivities(BaseModel):
    """Replace ``activities`` (adjacent steps of one sequence) with ``merged_name``."""

    op: Literal["merge_activities"] = "merge_activities"
    activities: list[str] = Field(min_length=2)
    merged_name: str


class AddCheck(BaseModel):
    """Insert a new control step ``name`` directly after the existing ``after``."""

    op: Literal["add_check"] = "add_check"
    name: str
    after: str


class AutomateActivity(BaseModel):
    """Have the system perform ``activity`` instead of a person; it stays in place."""

    op: Literal["automate_activity"] = "automate_activity"
    activity: str


EditOperation = Annotated[
    RemoveActivity
    | ParallelizeActivities
    | ReorderActivities
    | AddActivity
    | MergeActivities
    | AddCheck
    | AutomateActivity,
    Field(discriminator="op"),
]

OPERATION_TYPES: tuple[type[BaseModel], ...] = (
    RemoveActivity,
    ParallelizeActivities,
    ReorderActivities,
    AddActivity,
    MergeActivities,
    AddCheck,
    AutomateActivity,
)


def existing_activities(op: EditOperation) -> list[str]:
    """Activities ``op`` refers to that must already exist in the as-is process."""
    match op:
        case RemoveActivity() | AutomateActivity():
            return [op.activity]
        case ParallelizeActivities() | MergeActivities():
            return list(op.activities)
        case ReorderActivities():
            return [op.activity, op.after]
        case AddActivity() | AddCheck():
            return [op.after]
    raise TypeError(f"unknown edit operation: {op!r}")


def modified_activities(op: EditOperation) -> list[str]:
    """Activities ``op`` removes, moves or rewrites (checked against protection).

    WHY anchors and automation are excluded: inserting a step *after* a
    protected activity leaves it intact, and automating it keeps it in place
    with the same control purpose (user decision, Phase 4).
    """
    match op:
        case RemoveActivity() | ReorderActivities():
            return [op.activity]
        case ParallelizeActivities() | MergeActivities():
            return list(op.activities)
        case AddActivity() | AddCheck() | AutomateActivity():
            return []
    raise TypeError(f"unknown edit operation: {op!r}")


def new_activities(op: EditOperation) -> list[str]:
    """Activity names ``op`` introduces."""
    match op:
        case AddActivity() | AddCheck():
            return [op.name]
        case MergeActivities():
            return [] if op.merged_name in op.activities else [op.merged_name]
    return []


def describe(op: EditOperation) -> str:
    """One readable line per operation, for reports and diffs."""
    match op:
        case RemoveActivity():
            return f"remove '{op.activity}'"
        case AutomateActivity():
            return f"automate '{op.activity}'"
        case AddActivity():
            return f"add '{op.name}' after '{op.after}'"
        case AddCheck():
            return f"add check '{op.name}' after '{op.after}'"
        case ReorderActivities():
            return f"move '{op.activity}' to directly after '{op.after}'"
        case ParallelizeActivities():
            return "run " + ", ".join(f"'{a}'" for a in op.activities) + " in parallel"
        case MergeActivities():
            names = ", ".join(f"'{a}'" for a in op.activities)
            return f"merge {names} into '{op.merged_name}'"
    raise TypeError(f"unknown edit operation: {op!r}")


class EditError(ValueError):
    """An operation that cannot be applied to the model, with the reason."""

    def __init__(self, index: int, op: EditOperation, reason: str) -> None:
        """Keep the position and the reason for the agent's retry message."""
        self.index = index
        self.op = op
        self.reason = reason
        super().__init__(f"operation {index} ({describe(op)}): {reason}")


# --- tree navigation (paths are tuples of child indices) ---------------------

Path = tuple[int, ...]


def _find(tree: TreeNode, label: str, path: Path = ()) -> Path | None:
    if tree.is_leaf:
        return path if tree.label == label else None
    for i, child in enumerate(tree.children):
        found = _find(child, label, (*path, i))
        if found is not None:
            return found
    return None


def _get(tree: TreeNode, path: Path) -> TreeNode:
    for i in path:
        tree = tree.children[i]
    return tree


def _replace(tree: TreeNode, path: Path, new: TreeNode | list[TreeNode]) -> TreeNode:
    """Replace the node at ``path`` (a list splices several nodes in its place)."""
    if not path:
        assert isinstance(new, TreeNode), "the root cannot be spliced"
        return new
    head, rest = path[0], path[1:]
    children = list(tree.children)
    if rest:
        children[head] = _replace(children[head], rest, new)
    else:
        children[head : head + 1] = new if isinstance(new, list) else [new]
    return tree.model_copy(update={"children": children})


def _replace_run(tree: TreeNode, parent: Path, start: int, stop: int, new: TreeNode) -> TreeNode:
    """Replace children ``start..stop`` of the node at ``parent`` with ``new``."""
    target = _get(tree, parent)
    children = [*target.children[:start], new, *target.children[stop + 1 :]]
    return _replace(tree, parent, target.model_copy(update={"children": children}))


# --- application ---------------------------------------------------------------


def _locate(tree: TreeNode, label: str, index: int, op: EditOperation) -> Path:
    path = _find(tree, label)
    if path is None:
        raise EditError(index, op, f"'{label}' is not an activity of the current model")
    return path


def _adjacent_run(tree: TreeNode, names: list[str], index: int, op: EditOperation) -> tuple:
    """Parent path and child range of ``names`` as adjacent steps of one sequence."""
    if len(set(names)) != len(names):
        raise EditError(index, op, "an activity is listed more than once")
    paths = [_locate(tree, n, index, op) for n in names]
    parents = {p[:-1] for p in paths}
    parent = paths[0][:-1]
    is_root = any(len(p) == 0 for p in paths)
    if is_root or len(parents) != 1 or _get(tree, parent).operator != "sequence":
        raise EditError(
            index,
            op,
            "the activities must be steps of one sequence; at least one of them sits "
            "inside a choice, loop or parallel block, or they are in different branches",
        )
    positions = sorted(p[-1] for p in paths)
    if positions[-1] - positions[0] + 1 != len(positions):
        seq = _get(tree, parent)
        between = [
            ", ".join(labels(seq.children[i])) or "a silent step"
            for i in range(positions[0], positions[-1] + 1)
            if i not in positions
        ]
        raise EditError(
            index, op, f"the activities are not adjacent; between them: {'; '.join(between)}"
        )
    return parent, positions[0], positions[-1]


def _insert_after(
    tree: TreeNode, anchor: str, new: TreeNode, index: int, op: EditOperation
) -> TreeNode:
    path = _locate(tree, anchor, index, op)
    return _replace(tree, path, node("sequence", _get(tree, path), new))


def apply_operation(
    tree: TreeNode, op: EditOperation, index: int = 0, protected: frozenset[str] = frozenset()
) -> TreeNode:
    """Apply one operation; ``EditError`` if it cannot be applied as specified."""
    touched = [a for a in modified_activities(op) if a in protected]
    if touched:
        raise EditError(
            index, op, f"{touched} protected: may not be removed, moved, merged or parallelized"
        )
    present = set(labels(tree))
    for name in new_activities(op):
        if name in present:
            raise EditError(index, op, f"'{name}' already exists in the model")

    match op:
        case RemoveActivity():
            out = _replace(tree, _locate(tree, op.activity, index, op), leaf(None))
        case AutomateActivity():
            path = _locate(tree, op.activity, index, op)
            current = _get(tree, path)
            if current.automated:
                raise EditError(index, op, f"'{op.activity}' is already automated")
            out = _replace(tree, path, current.model_copy(update={"automated": True}))
        case AddActivity():
            out = _insert_after(tree, op.after, leaf(op.name), index, op)
        case AddCheck():
            out = _insert_after(tree, op.after, leaf(op.name, kind="check"), index, op)
        case ReorderActivities():
            if op.activity == op.after:
                raise EditError(index, op, "an activity cannot be moved after itself")
            path = _locate(tree, op.activity, index, op)
            moving = _get(tree, path)
            without = normalize(_replace(tree, path, leaf(None)))
            out = _insert_after(without, op.after, moving, index, op)
        case ParallelizeActivities():
            parent, start, stop = _adjacent_run(tree, op.activities, index, op)
            block = node("parallel", *_get(tree, parent).children[start : stop + 1])
            out = _replace_run(tree, parent, start, stop, block)
        case MergeActivities():
            parent, start, stop = _adjacent_run(tree, op.activities, index, op)
            out = _replace_run(tree, parent, start, stop, leaf(op.merged_name))
        case _:
            raise TypeError(f"unknown edit operation: {op!r}")
    out = normalize(out)
    assert_unique_labels(out)
    return out


def apply_operations(
    tree: TreeNode, ops: list[EditOperation], protected: frozenset[str] = frozenset()
) -> TreeNode:
    """Apply ``ops`` in order; the first that cannot be applied raises ``EditError``."""
    for i, op in enumerate(ops):
        tree = apply_operation(tree, op, i, protected)
    return tree


class ModelDiff(BaseModel):
    """What changed between an as-is and a to-be tree."""

    changes: list[str]
    added: list[str]
    removed: list[str]
    checks_added: list[str]
    automated: list[str]
    as_is: str
    to_be: str


def diff_trees(as_is: TreeNode, to_be: TreeNode, ops: list[EditOperation]) -> ModelDiff:
    """Operation descriptions plus the activity-level difference of the two trees."""
    before = {lf.label: lf for lf in leaves(as_is)}
    after = {lf.label: lf for lf in leaves(to_be)}
    return ModelDiff(
        changes=[describe(op) for op in ops],
        added=sorted(set(after) - set(before)),  # type: ignore[type-var]
        removed=sorted(set(before) - set(after)),  # type: ignore[type-var]
        checks_added=sorted(
            lab for lab, lf in after.items() if lf.kind == "check" and lab not in before
        ),  # type: ignore[type-var]
        automated=sorted(
            lab
            for lab, lf in after.items()
            if lf.automated and not (lab in before and before[lab].automated)
        ),  # type: ignore[type-var]
        as_is=to_text(as_is),
        to_be=to_text(to_be),
    )
