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
derive from the node's position in the tree. The rendering also merges gateways
that route nothing, so the drawing has fewer shapes than the tree has operators.

pm4py is still used for discovery (``discover_process_tree_inductive``),
writing BPMN XML, rendering PNGs and the Petri-net conversion behind the
soundness check (all verified against pm4py 2.7.23.11). Layout calls Graphviz
directly when it is available.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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

# Drawing sizes in BPMN units: bpmn-js's own defaults for gateways and events,
# and a task tall enough for two lines of text above a loop marker.
TASK_SIZE = (120, 76)
GATEWAY_SIZE = (50, 50)
EVENT_SIZE = (36, 36)
GRAPHVIZ_POINTS_PER_INCH = 72
# Polyline, not orthogonal: tree-structured models then have no crossings at all,
# where Graphviz's orthogonal router adds some and stacks flows on one track.
GRAPHVIZ_GRAPH_ATTRIBUTES = "rankdir=LR; splines=polyline; nodesep=0.5; ranksep=0.5;"


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


def _is_simple_repeat(tree: TreeNode) -> bool:
    """True for ``*('A', tau)``: one visible task, repeated with nothing in between."""
    if tree.operator != "loop" or len(tree.children) != 2:
        return False
    do, redo = tree.children
    return do.is_leaf and not do.is_silent and redo.is_silent


class _BpmnBuilder:
    """Emits BPMN elements for a tree with ids that depend only on the tree.

    Elements are collected first and handed to pm4py by ``finish``, because a
    pm4py flow cannot be detached from its nodes once created and gateways
    are merged after the whole tree has been emitted.
    """

    def __init__(self, process_id: str, loop_markers: bool = False) -> None:
        from pm4py.objects.bpmn.obj import BPMN

        self.B = BPMN
        self.process_id = process_id
        self.loop_markers = loop_markers
        self.loop_tasks: set[str] = set()
        self._nodes: dict[str, Any] = {}
        self._flows: dict[tuple[str, str], None] = {}  # insertion-ordered set
        self._task_ids: dict[str, str] = {}
        self.bpmn: BPMN | None = None

    def add(self, element: Any) -> Any:
        self._nodes[element.get_id()] = element
        return element

    def connect(self, src: Any, dst: Any) -> None:
        self._flows[(src.get_id(), dst.get_id())] = None

    def _contractible(self) -> tuple[str, str] | None:
        """The first flow between two gateways that routes nothing by itself."""
        n_in = Counter(dst for _, dst in self._flows)
        n_out = Counter(src for src, _ in self._flows)
        for src, dst in self._flows:
            a, b = self._nodes[src], self._nodes[dst]
            if type(a) is not type(b) or not isinstance(a, self.B.Gateway):
                continue
            if n_out[src] == 1 or n_in[dst] == 1 or (dst, src) in self._flows:
                return src, dst
        return None

    def _contract_gateways(self) -> None:
        """Merge gateways of one type wherever a flow between them decides nothing.

        WHY: rendering each tree operator as its own split/join pair is correct
        but leaves chains of empty diamonds (BPI 2019: 94 gateways around 33
        tasks). In a block-structured model an exclusive gateway is a Petri-net
        place and a parallel gateway a transition, so a flow between two of the
        same type is a silent step, and removing it keeps the traces when
        nothing else can happen at that point: the source has no other exit,
        the target has no other entrance, or (exclusive only; a parallel block
        has no cycles) the two lead straight back to each other. A skip flow
        from a real split to a real join meets none of these and stays.
        """
        while (pair := self._contractible()) is not None:
            keep, drop = pair
            del self._nodes[drop]
            flows = ((keep if s == drop else s, keep if d == drop else d) for s, d in self._flows)
            self._flows = {flow: None for flow in flows if flow[0] != flow[1]}

    def finish(self) -> None:
        """Build ``self.bpmn``, with each gateway's direction taken from its flows."""
        self._contract_gateways()
        direction = self.B.Gateway.Direction
        n_in = Counter(dst for _, dst in self._flows)
        n_out = Counter(src for src, _ in self._flows)
        bpmn = self.B(process_id=self.process_id)
        for element_id, element in self._nodes.items():
            if isinstance(element, self.B.Gateway):
                # Unspecified is BPMN's value for a gateway that both joins and
                # splits; pm4py has no separate "mixed".
                element.set_gateway_direction(
                    direction.DIVERGING
                    if n_in[element_id] == 1
                    else direction.CONVERGING
                    if n_out[element_id] == 1
                    else direction.UNSPECIFIED
                )
            bpmn.add_node(element)
        for src, dst in self._flows:
            bpmn.add_flow(
                self.B.SequenceFlow(
                    self._nodes[src],
                    self._nodes[dst],
                    id=f"flow__{src}__{dst}",
                    process=self.process_id,
                )
            )
        self.bpmn = bpmn

    def _shape(self, element: Any) -> tuple[str, tuple[int, int]]:
        if isinstance(element, self.B.Gateway):
            return "diamond", GATEWAY_SIZE
        if isinstance(element, self.B.Task):
            return "box", TASK_SIZE
        return "circle", EVENT_SIZE

    def _dot_source(self) -> str:
        lines = [f"digraph {{ {GRAPHVIZ_GRAPH_ATTRIBUTES}"]
        for element_id, element in self._nodes.items():
            shape, (width, height) = self._shape(element)
            lines.append(
                f'"{element_id}" [shape={shape}, fixedsize=true, label="", '
                f"width={width / GRAPHVIZ_POINTS_PER_INCH}, "
                f"height={height / GRAPHVIZ_POINTS_PER_INCH}];"
            )
        lines.extend(f'"{src}" -> "{dst}" [arrowhead=none];' for src, dst in self._flows)
        return "\n".join([*lines, "}"])

    def layout(self) -> None:
        """Place shapes and route flows on ``self.bpmn`` with Graphviz.

        WHY not pm4py's layouter: it sizes shapes from a text label, so tasks
        come out too low for a loop marker and the text, and it clips every
        flow to a rectangle, leaving arrows short of the diamonds. Here Graphviz
        gets BPMN's real shapes and sizes, so flows end on the outline. Elements
        go in in emission order, which keeps a block's steps together and makes
        the layout depend only on the tree.
        """
        assert self.bpmn is not None
        done = subprocess.run(
            ["dot", "-Tjson"], input=self._dot_source(), capture_output=True, text=True, check=True
        )
        graph = json.loads(done.stdout)
        top = float(graph["bb"].split(",")[3])  # Graphviz's y axis points up, BPMN's down
        names = {}
        for placed in graph["objects"]:
            names[placed["_gvid"]] = placed["name"]
            element = self._nodes[placed["name"]]
            _, (width, height) = self._shape(element)
            x, y = (float(v) for v in placed["pos"].split(","))
            element.set_x(round(x - width / 2, 1))
            element.set_y(round(top - y - height / 2, 1))
            element.set_width(width)
            element.set_height(height)
        flows = {
            (f.get_source().get_id(), f.get_target().get_id()): f for f in self.bpmn.get_flows()
        }
        for edge in graph["edges"]:
            flow = flows[(names[edge["tail"]], names[edge["head"]])]
            points = [tuple(map(float, p.split(","))) for p in edge["pos"].split()]
            flow.del_waypoints()
            # A polyline comes as Bezier segments sharing every third point.
            for x, y in points[::3]:
                flow.add_waypoint((round(x, 1), round(top - y, 1)))

    def task_id(self, label: str) -> str:
        base = f"task_{_slug(label)}"
        candidate, n = base, 2
        while candidate in self._task_ids.values():
            candidate, n = f"{base}_{n}", n + 1
        self._task_ids[label] = candidate
        return candidate

    def gateway(self, op: Operator, path: str, role: str) -> Any:
        cls = self.B.ParallelGateway if op == "parallel" else self.B.ExclusiveGateway
        return self.add(cls(id=f"gw_{path}_{role}", process=self.process_id))

    def emit(self, tree: TreeNode, path: str, src: Any) -> Any:
        """Build ``tree`` after ``src``; return the element its exit is."""
        if tree.is_silent:
            return src
        if self.loop_markers and _is_simple_repeat(tree):
            task = self.emit(tree.children[0], f"{path}_0", src)
            self.loop_tasks.add(task.get_id())
            return task
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


def _render(tree: TreeNode, process_id: str, loop_markers: bool) -> _BpmnBuilder:
    """The finished builder of a tree's BPMN model."""
    b = _BpmnBuilder(process_id, loop_markers)
    start = b.add(b.B.NormalStartEvent(id="start", name="start", process=process_id))
    end = b.add(b.B.NormalEndEvent(id="end", name="end", process=process_id))
    b.connect(b.emit(tree, "n", start), end)
    b.finish()
    return b


def to_bpmn(tree: TreeNode, process_id: str = "process") -> BPMN:
    """Render a tree as a pm4py BPMN object with deterministic element ids.

    Every loop is explicit here, so the soundness check and the PNG (pm4py
    draws neither from a loop marker) see the model's full control flow.
    """
    return _render(tree, process_id, loop_markers=False).bpmn  # type: ignore[return-value]


def graphviz_available() -> bool:
    """Whether Graphviz ``dot`` is on PATH (needed for layout and PNGs)."""
    return shutil.which("dot") is not None


def _strip_blank_text(element: Any) -> None:
    for child in list(element.childNodes):
        if child.nodeType == child.TEXT_NODE and not child.data.strip():
            element.removeChild(child)
        elif child.nodeType == child.ELEMENT_NODE:
            _strip_blank_text(child)


def canonical_bpmn_xml(
    xml: bytes, process_id: str, loop_tasks: frozenset[str] | set[str] = frozenset()
) -> bytes:
    """pm4py's exported BPMN XML in a byte-stable form, with loop markers added.

    WHY: pm4py's exporter writes nodes and flows in set order (object hashes,
    so it changes between runs) and gives the diagram and its plane fresh
    random ids on every write, so the same tree never produced the same file.
    Element order inside a process or plane carries no meaning in BPMN, so
    sorting by tag and id loses nothing; edges sort before shapes, which also
    makes renderers draw arrows underneath tasks. Markers are written here
    because pm4py's model has no loop characteristics to export.
    """
    from xml.dom import minidom

    doc = minidom.parseString(xml)
    _strip_blank_text(doc.documentElement)
    for task in doc.getElementsByTagName("bpmn:task"):
        if task.getAttribute("id") in loop_tasks:
            task.appendChild(doc.createElement("bpmn:standardLoopCharacteristics"))
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
    BPMN files can be diffed between runs like the evidence. A task repeated
    on its own is drawn with BPMN's loop marker instead of two gateways and a
    back-arrow: same behaviour, and far fewer crossings in the layout. Without
    Graphviz the model is still complete and re-readable, but every shape sits
    at (0, 0); callers record which happened.
    """
    from pm4py.objects.bpmn.exporter.variants import etree as bpmn_exporter

    layout = graphviz_available()
    built = _render(tree, process_id, loop_markers=True)
    if layout:
        built.layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    xml = bpmn_exporter.get_xml_string(built.bpmn)
    path.write_bytes(canonical_bpmn_xml(xml, process_id, built.loop_tasks))
    return layout


def render_png(tree: TreeNode, path: Path) -> bool:
    """Draw the tree's BPMN to a PNG; returns ``False`` (no file) without Graphviz."""
    if not graphviz_available():
        return False
    import pm4py

    path.parent.mkdir(parents=True, exist_ok=True)
    pm4py.save_vis_bpmn(to_bpmn(tree), str(path))
    return True
