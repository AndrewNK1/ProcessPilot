"""Process trees: the editable form of a process model, and its BPMN rendering.

WHY edits are applied to a process tree and not to BPMN: the inductive miner
produces a block-structured tree (sequence, exclusive choice, parallel, loop),
and every such tree corresponds to a sound workflow net. Editing the tree and
rendering it again keeps every to-be model sound by construction, whereas
rewiring BPMN arcs by hand can leave dead ends or unreachable tasks.

WHY our own model instead of pm4py's ``ProcessTree``: it is a typed, JSON-
serialisable value (written next to the as-is BPMN and read by the agent
layer without opening event data), it can carry ProcessPilot's own markers
(``automated``, ``kind="check"``), and its BPMN rendering assigns
deterministic element ids. pm4py's converter assigns fresh random UUIDs on
every export, which would break the planned bottleneck overlay and any diff
between runs. Task ids derive from the activity name, so ``task_record_goods_
receipt`` is the same element in the as-is and every to-be model; gateway ids
derive from the node's position in the tree.

pm4py is still used for discovery (``discover_process_tree_inductive``),
writing BPMN XML (with Graphviz layout when available), rendering PNGs and the
Petri-net conversion behind the soundness check (all verified against pm4py
2.7.23.11).
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from pm4py.objects.bpmn.obj import BPMN
    from pm4py.objects.process_tree.obj import ProcessTree

Operator = Literal["sequence", "xor", "parallel", "loop"]
NodeKind = Literal["activity", "check"]

_PM4PY_TO_OPERATOR = {"SEQUENCE": "sequence", "XOR": "xor", "PARALLEL": "parallel", "LOOP": "loop"}
_SYMBOL = {"sequence": "->", "xor": "X", "parallel": "+", "loop": "*"}
AUTOMATED_SUFFIX = " (automated)"


class TreeNode(BaseModel):
    """One node: an operator with children, a visible activity, or silent (tau).

    A leaf with ``label=None`` is a silent step (it does nothing; an XOR with a
    silent child makes its other children optional).
    """

    operator: Operator | None = None
    label: str | None = None
    kind: NodeKind = "activity"
    automated: bool = False
    children: list[TreeNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> TreeNode:
        if self.operator is None:
            if self.children:
                raise ValueError("a leaf cannot have children")
        else:
            if self.label is not None:
                raise ValueError("an operator node has no label")
            minimum = 2 if self.operator == "loop" else 1
            if len(self.children) < minimum:
                raise ValueError(f"{self.operator} needs at least {minimum} child(ren)")
        return self

    @property
    def is_leaf(self) -> bool:
        """True for activities and silent steps."""
        return self.operator is None

    @property
    def is_silent(self) -> bool:
        """True for a tau leaf."""
        return self.operator is None and self.label is None

    def display_name(self) -> str:
        """Task name as drawn: the label, marked when automated."""
        assert self.label is not None
        return self.label + (AUTOMATED_SUFFIX if self.automated else "")


def leaf(label: str | None, **kw: Any) -> TreeNode:
    """A visible activity, or a silent step when ``label`` is ``None``."""
    return TreeNode(label=label, **kw)


def node(operator: Operator, *children: TreeNode) -> TreeNode:
    """An operator node."""
    return TreeNode(operator=operator, children=list(children))


def labels(tree: TreeNode) -> list[str]:
    """Visible activity labels, in depth-first order."""
    if tree.is_leaf:
        return [] if tree.label is None else [tree.label]
    return [lab for child in tree.children for lab in labels(child)]


def leaves(tree: TreeNode) -> list[TreeNode]:
    """Visible activity leaves, in depth-first order."""
    if tree.is_leaf:
        return [] if tree.is_silent else [tree]
    return [lf for child in tree.children for lf in leaves(child)]


def assert_unique_labels(tree: TreeNode) -> None:
    """Edits address activities by name, so a name must occur once."""
    dupes = sorted(lab for lab, n in Counter(labels(tree)).items() if n > 1)
    if dupes:
        raise ValueError(f"activity label(s) occur more than once in the tree: {dupes}")


def normalize(tree: TreeNode) -> TreeNode:
    """Simplify without changing behaviour.

    Drops silent children of sequence/parallel nodes, flattens a sequence
    inside a sequence (and parallel inside parallel), keeps at most one silent
    child of an XOR, and replaces a sequence/parallel/XOR with one child by
    that child. Loops keep their shape (their children are positional) unless
    every child is silent, in which case the loop is itself silent.
    """
    if tree.is_leaf:
        return tree
    children = [normalize(c) for c in tree.children]
    op = tree.operator
    if op in ("sequence", "parallel"):
        flat: list[TreeNode] = []
        for c in children:
            if c.is_silent:
                continue
            flat.extend(c.children if c.operator == op else [c])
        children = flat
        if not children:
            return leaf(None)
    elif op == "xor":
        out: list[TreeNode] = []
        for c in children:
            if c.operator == "xor":
                out.extend(c.children)
            else:
                out.append(c)
        silent = [c for c in out if c.is_silent]
        children = [c for c in out if not c.is_silent] + silent[:1]
    elif op == "loop" and all(c.is_silent for c in children):
        # Repeating nothing is nothing (left behind when a loop's only task is removed).
        return leaf(None)
    if op != "loop" and len(children) == 1:
        return children[0]
    return tree.model_copy(update={"children": children})


def to_text(tree: TreeNode) -> str:
    """Compact one-line rendering, e.g. ``->('A', X('B', tau))``; for logs and diffs."""
    if tree.is_leaf:
        if tree.is_silent:
            return "tau"
        marks = ("check " if tree.kind == "check" else "") + (
            "automated " if tree.automated else ""
        )
        return f"{marks}'{tree.label}'"
    inner = ", ".join(to_text(c) for c in tree.children)
    return f"{_SYMBOL[tree.operator]}({inner})"  # type: ignore[index]


# --- pm4py interop -----------------------------------------------------------


def from_pm4py(pt: ProcessTree) -> TreeNode:
    """Convert a pm4py process tree (sequence/XOR/parallel/loop only)."""
    if pt.operator is None:
        return leaf(pt.label)
    name = pt.operator.name
    if name not in _PM4PY_TO_OPERATOR:
        raise ValueError(f"unsupported process-tree operator {name}")
    return node(_PM4PY_TO_OPERATOR[name], *(from_pm4py(c) for c in pt.children))  # type: ignore[arg-type]


def discover_tree(df: Any, noise_threshold: float) -> TreeNode:
    """Inductive-miner tree of a canonical log (pm4py, then normalised)."""
    import pm4py

    from processpilot.schema import (
        PM4PY_ACTIVITY,
        PM4PY_CASE_ID,
        PM4PY_TIMESTAMP,
        to_pm4py,
    )

    log = to_pm4py(df[["case_id", "activity", "timestamp"]])
    pt = pm4py.discover_process_tree_inductive(
        log,
        noise_threshold=noise_threshold,
        activity_key=PM4PY_ACTIVITY,
        timestamp_key=PM4PY_TIMESTAMP,
        case_id_key=PM4PY_CASE_ID,
    )
    tree = normalize(from_pm4py(pt))
    assert_unique_labels(tree)
    return tree


# --- BPMN rendering ------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


class _BpmnBuilder:
    """Emits BPMN elements for a tree with ids that depend only on the tree."""

    def __init__(self, process_id: str) -> None:
        from pm4py.objects.bpmn.obj import BPMN

        self.B = BPMN
        self.process_id = process_id
        self.bpmn = BPMN(process_id=process_id)
        self._nodes: dict[str, Any] = {}
        self._flows: set[tuple[str, str]] = set()
        self._task_ids: dict[str, str] = {}

    def add(self, element: Any) -> Any:
        self._nodes[element.get_id()] = element
        self.bpmn.add_node(element)
        return element

    def connect(self, src: Any, dst: Any) -> None:
        key = (src.get_id(), dst.get_id())
        if key in self._flows:
            return
        self._flows.add(key)
        self.bpmn.add_flow(
            self.B.SequenceFlow(src, dst, id=f"flow__{key[0]}__{key[1]}", process=self.process_id)
        )

    def task_id(self, label: str) -> str:
        base = f"task_{_slug(label)}"
        candidate, n = base, 2
        while candidate in self._task_ids.values():
            candidate, n = f"{base}_{n}", n + 1
        self._task_ids[label] = candidate
        return candidate

    def gateway(self, op: Operator, path: str, role: str) -> Any:
        direction = self.B.Gateway.Direction
        cls = self.B.ParallelGateway if op == "parallel" else self.B.ExclusiveGateway
        return self.add(
            cls(
                id=f"gw_{path}_{role}",
                gateway_direction=direction.CONVERGING
                if role in ("join", "loop_entry")
                else direction.DIVERGING,
                process=self.process_id,
            )
        )

    def emit(self, tree: TreeNode, path: str, src: Any) -> Any:
        """Build ``tree`` after ``src``; return the element its exit is."""
        if tree.is_silent:
            return src
        if tree.is_leaf:
            task = self.add(
                self.B.Task(
                    id=self.task_id(tree.label),  # type: ignore[arg-type]
                    name=tree.display_name(),
                    process=self.process_id,
                )
            )
            self.connect(src, task)
            return task
        kids = [(c, f"{path}_{i}") for i, c in enumerate(tree.children)]
        if tree.operator == "sequence":
            cur = src
            for child, p in kids:
                cur = self.emit(child, p, cur)
            return cur
        if tree.operator in ("xor", "parallel"):
            split = self.gateway(tree.operator, path, "split")  # type: ignore[arg-type]
            join = self.gateway(tree.operator, path, "join")  # type: ignore[arg-type]
            self.connect(src, split)
            for child, p in kids:
                self.connect(self.emit(child, p, split), join)
            return join
        # loop: do-part, then either exit or a redo-part and back to the start.
        entry = self.gateway("xor", path, "loop_entry")
        self.connect(src, entry)
        exit_ = self.gateway("xor", path, "loop_exit")
        self.connect(self.emit(kids[0][0], kids[0][1], entry), exit_)
        for child, p in kids[1:]:
            self.connect(self.emit(child, p, exit_), entry)
        return exit_


def to_bpmn(tree: TreeNode, process_id: str = "process") -> BPMN:
    """Render a tree as a pm4py BPMN object with deterministic element ids."""
    b = _BpmnBuilder(process_id)
    start = b.add(b.B.NormalStartEvent(id="start", name="start", process=process_id))
    end = b.add(b.B.NormalEndEvent(id="end", name="end", process=process_id))
    b.connect(b.emit(tree, "n", start), end)
    return b.bpmn


def graphviz_available() -> bool:
    """Whether Graphviz ``dot`` is on PATH (needed for layout and PNGs)."""
    return shutil.which("dot") is not None


def _strip_blank_text(element: Any) -> None:
    for child in list(element.childNodes):
        if child.nodeType == child.TEXT_NODE and not child.data.strip():
            element.removeChild(child)
        elif child.nodeType == child.ELEMENT_NODE:
            _strip_blank_text(child)


def canonical_bpmn_xml(xml: bytes, process_id: str) -> bytes:
    """pm4py's exported BPMN XML in a byte-stable form.

    WHY: pm4py's exporter writes nodes and flows in set order (object hashes,
    so it changes between runs) and gives the diagram and its plane fresh
    random ids on every write, so the same tree never produced the same file.
    Element order inside a process or plane carries no meaning in BPMN, so
    sorting by tag and id loses nothing; edges sort before shapes, which also
    makes renderers draw arrows underneath tasks.
    """
    from xml.dom import minidom

    doc = minidom.parseString(xml)
    _strip_blank_text(doc.documentElement)
    for tag in ("bpmn:process", "bpmndi:BPMNPlane"):
        for parent in doc.getElementsByTagName(tag):
            children = list(parent.childNodes)
            for child in children:
                parent.removeChild(child)
            for child in sorted(children, key=lambda c: (c.tagName, c.getAttribute("id"))):
                parent.appendChild(child)
    for tag, suffix in (("bpmndi:BPMNDiagram", "diagram"), ("bpmndi:BPMNPlane", "plane")):
        for i, element in enumerate(doc.getElementsByTagName(tag)):
            element.setAttribute("id", f"id{process_id}_{suffix}" + (f"_{i}" if i else ""))
    return doc.toprettyxml(encoding="utf-8")


def write_bpmn(tree: TreeNode, path: Path, process_id: str = "process") -> bool:
    """Write the tree as BPMN XML; returns whether a Graphviz layout was applied.

    The same tree always gives the same bytes (see ``canonical_bpmn_xml``), so
    BPMN files can be diffed between runs like the evidence. Without Graphviz
    the model is still complete and re-readable, but every shape sits at
    (0, 0); callers record which happened.
    """
    from pm4py.objects.bpmn.exporter.variants import etree as bpmn_exporter
    from pm4py.objects.bpmn.layout import layouter

    layout = graphviz_available()
    bpmn = to_bpmn(tree, process_id)
    if layout:
        bpmn = layouter.apply(bpmn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bpmn_xml(bpmn_exporter.get_xml_string(bpmn), process_id))
    return layout


def render_png(tree: TreeNode, path: Path) -> bool:
    """Draw the tree's BPMN to a PNG; returns ``False`` (no file) without Graphviz."""
    if not graphviz_available():
        return False
    import pm4py

    path.parent.mkdir(parents=True, exist_ok=True)
    pm4py.save_vis_bpmn(to_bpmn(tree), str(path))
    return True
