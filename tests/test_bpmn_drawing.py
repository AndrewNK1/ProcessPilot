"""Compact BPMN drawing: fewer shapes, the same process.

Fewer gateways are only an improvement if the behaviour is untouched, so the
key tests here compare trace sets: ``tree_language`` computes what a process
tree allows from its definition, and ``file_language`` plays the BPMN token
game on the file ``write_bpmn`` actually wrote, loop markers included. Both are
cut at ``LIMIT`` activities, which bounds the loops.

Hand-worked gateway counts, with A and B each "optional, may repeat":
``->(X(*('A', tau), tau), X(*('B', tau), tau))``
    split/join for each X, entry/exit for each loop = 8 before merging;
    file: G1 -> A -> G2 -> B -> G3, with skips G1 -> G2 and G2 -> G3 = 3.
"""

from __future__ import annotations

import importlib.util
import itertools
import random
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import pytest

import processpilot.process_tree as pt
from processpilot.agent.state import RedesignSet
from processpilot.process_tree import TreeNode, leaf, node, normalize, to_bpmn, to_text, write_bpmn
from processpilot.redesign.bpmn_edit import apply_proposal, load_tree, save_tree, write_to_be
from processpilot.redesign.soundness import check_soundness, structural_issues

LIMIT = 5
BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
BPMNDI_NS = "{http://www.omg.org/spec/BPMN/20100524/DI}"
DC_NS = "{http://www.omg.org/spec/DD/20100524/DC}"
DI_NS = "{http://www.omg.org/spec/DD/20100524/DI}"
Trace = tuple[str, ...]

A, B, C, D = leaf("A"), leaf("B"), leaf("C"), leaf("D")
TAU = leaf(None)


def repeat(t: TreeNode) -> TreeNode:
    return node("loop", t, TAU)


def optional(t: TreeNode) -> TreeNode:
    return node("xor", t, TAU)


SHAPES = {
    "optional-repeats": node("sequence", optional(repeat(A)), optional(repeat(B))),
    "repeat": repeat(A),
    "repeat-group": repeat(node("xor", A, B)),
    "repeat-optional": repeat(optional(A)),
    "silent-do": node("loop", TAU, A),
    "loop-with-redo": node("loop", A, B),
    "xor-then-xor": node("sequence", node("xor", A, B), node("xor", C, D)),
    "xor-ending-in-xor": node("xor", node("sequence", A, node("xor", B, C)), D),
    "xor-starting-with-xor": node("xor", node("sequence", node("xor", A, B), C), D),
    "parallel-then-parallel": node("sequence", node("parallel", A, B), node("parallel", C, D)),
    "parallel-ending-in-parallel": node("parallel", node("sequence", A, node("parallel", B, C)), D),
    "parallel-of-optionals": node("parallel", optional(A), optional(repeat(B))),
    "xor-next-to-parallel": node("sequence", node("xor", A, B), node("parallel", C, D)),
}


def tree_language(t: TreeNode, limit: int = LIMIT) -> set[Trace]:
    """Traces a tree allows, by definition of its operators."""
    if t.is_leaf:
        return {()} if t.is_silent else {(t.label,)}  # type: ignore[dict-item]
    parts = [tree_language(c, limit) for c in t.children]
    if t.operator == "xor":
        return set().union(*parts)

    def then(xs: set[Trace], ys: set[Trace]) -> set[Trace]:
        return {x + y for x in xs for y in ys if len(x) + len(y) <= limit}

    if t.operator == "sequence":
        out = {()}
        for part in parts:
            out = then(out, part)
        return out
    if t.operator == "parallel":
        out = {()}
        for part in parts:
            out = {m for x in out for y in part if len(x) + len(y) <= limit for m in _shuffle(x, y)}
        return out
    out = set(parts[0])
    while True:
        grown = out | then(then(out, set().union(*parts[1:])), parts[0])
        if grown == out:
            return out
        out = grown


def _shuffle(x: Trace, y: Trace) -> set[Trace]:
    if not x or not y:
        return {x + y}
    return {(x[0], *m) for m in _shuffle(x[1:], y)} | {(y[0], *m) for m in _shuffle(x, y[1:])}


class Graph:
    """A written BPMN file as elements and flows."""

    def __init__(self, path: Path) -> None:
        """Parse the process element of a written file."""
        process = next(ET.parse(path).getroot().iter(f"{BPMN_NS}process"))
        self.kind: dict[str, str] = {}
        self.label: dict[str, str] = {}
        self.looped: set[str] = set()
        self.flows: list[tuple[str, str]] = []
        for el in process:
            tag = el.tag.removeprefix(BPMN_NS)
            if tag == "sequenceFlow":
                self.flows.append((el.attrib["sourceRef"], el.attrib["targetRef"]))
                continue
            self.kind[el.attrib["id"]] = tag
            self.label[el.attrib["id"]] = el.attrib.get("name", "")
            if el.find(f"{BPMN_NS}standardLoopCharacteristics") is not None:
                self.looped.add(el.attrib["id"])

    def gateways(self) -> list[str]:
        """Ids of all gateways."""
        return [i for i, k in self.kind.items() if k.endswith("Gateway")]

    def tasks(self) -> list[str]:
        """Ids of all tasks."""
        return [i for i, k in self.kind.items() if k == "task"]


def file_language(g: Graph, limit: int = LIMIT) -> set[Trace]:
    """Traces a written file allows: the token game, with a marked task repeatable."""
    incoming: dict[str, list[tuple[str, str]]] = defaultdict(list)
    outgoing: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for flow in g.flows:
        outgoing[flow[0]].append(flow)
        incoming[flow[1]].append(flow)
    start = next(i for i, k in g.kind.items() if k == "startEvent")

    def key(marking: Counter) -> tuple:
        return tuple(sorted(marking.elements()))

    first = (key(Counter(outgoing[start])), ())
    seen, stack, done = {first}, [first], set()
    while stack:
        tokens, trace = stack.pop()
        marking = Counter(tokens)
        steps: list[tuple[Counter, Trace]] = []
        for element, kind in g.kind.items():
            enabled = [f for f in incoming[element] if marking[f]]
            if kind == "endEvent" and enabled and sum(marking.values()) == 1:
                done.add(trace)
            elif kind == "task" and enabled and len(trace) < limit:
                after = (*trace, g.label[element])
                steps.append(
                    (marking - Counter(incoming[element]) + Counter(outgoing[element]), after)
                )
                if element in g.looped:
                    steps.append((marking, after))
            elif kind == "exclusiveGateway":
                for f, out in itertools.product(enabled, outgoing[element]):
                    steps.append((marking - Counter([f]) + Counter([out]), trace))
            elif kind == "parallelGateway" and len(enabled) == len(incoming[element]):
                steps.append(
                    (marking - Counter(incoming[element]) + Counter(outgoing[element]), trace)
                )
        for next_marking, next_trace in steps:
            state = (key(next_marking), next_trace)
            if state not in seen:
                seen.add(state)
                stack.append(state)
    return done


def random_tree(rng: random.Random, depth: int, names: list[str]) -> TreeNode:
    if depth == 0 or len(names) < 2 or rng.random() < 0.25:
        return leaf(names.pop()) if names else TAU
    op = rng.choice(["sequence", "xor", "parallel", "loop"])
    if op == "loop":
        redo = TAU if rng.random() < 0.7 else random_tree(rng, depth - 1, names)
        return node("loop", random_tree(rng, depth - 1, names), redo)
    kids = [random_tree(rng, depth - 1, names) for _ in range(rng.randint(2, 3))]
    if op == "xor" and rng.random() < 0.6:
        kids.append(TAU)
    return node(op, *kids)  # type: ignore[arg-type]


@pytest.fixture
def written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Writes a tree without Graphviz (the graph is what is under test here)."""
    monkeypatch.setattr(pt, "graphviz_available", lambda: False)

    def write(t: TreeNode) -> Graph:
        write_bpmn(t, tmp_path / "t.bpmn")
        return Graph(tmp_path / "t.bpmn")

    return write


@pytest.mark.parametrize("t", SHAPES.values(), ids=SHAPES.keys())
def test_written_file_allows_exactly_the_trees_traces(t: TreeNode, written) -> None:
    assert file_language(written(t)) == tree_language(t)


@pytest.mark.parametrize("seed", range(150))
def test_random_trees_keep_their_traces(seed: int, written) -> None:
    rng = random.Random(seed)
    t = normalize(random_tree(rng, 4, list("ABCDEF")))
    assert file_language(written(t)) == tree_language(t), to_text(t)


@pytest.mark.parametrize("t", SHAPES.values(), ids=SHAPES.keys())
def test_merged_models_stay_sound(t: TreeNode) -> None:
    assert structural_issues(to_bpmn(t)) == []
    result = check_soundness(t, woflan_max_transitions=40)
    assert result.sound and result.woflan == "sound"


def test_optional_repeating_steps_need_one_gateway_each_plus_one(written) -> None:
    g = written(SHAPES["optional-repeats"])
    assert len(g.gateways()) == 3
    assert g.looped == {"task_a", "task_b"}


def test_repeated_task_needs_no_gateways_in_the_file(written) -> None:
    g = written(node("sequence", A, repeat(B), C))
    assert g.gateways() == []
    assert g.looped == {"task_b"}
    assert ("start", "task_a") in g.flows and ("task_b", "task_c") in g.flows


def test_repeat_group_keeps_its_gateways_and_gets_no_marker(written) -> None:
    g = written(SHAPES["repeat-group"])
    assert g.looped == set()
    assert len(g.gateways()) == 2


def test_join_then_split_is_one_gateway(written) -> None:
    g = written(SHAPES["xor-then-xor"])
    assert len(g.gateways()) == 3


def test_skip_between_a_real_split_and_join_stays(written) -> None:
    g = written(node("sequence", optional(A), B))
    assert len(g.gateways()) == 2
    assert set(itertools.permutations(g.gateways(), 2)) & set(g.flows)


def test_gateways_of_different_types_are_not_merged(written) -> None:
    g = written(SHAPES["xor-next-to-parallel"])
    assert Counter(g.kind[i] for i in g.gateways()) == {
        "exclusiveGateway": 2,
        "parallelGateway": 2,
    }


def test_explicit_model_keeps_the_loop_for_soundness() -> None:
    bpmn = to_bpmn(node("sequence", A, repeat(B), C))
    assert sum(type(n).__name__ == "ExclusiveGateway" for n in bpmn.get_nodes()) == 2


@pytest.mark.parametrize("t", SHAPES.values(), ids=SHAPES.keys())
def test_no_gateway_is_a_pass_through(t: TreeNode, written) -> None:
    g = written(t)
    n_in = Counter(dst for _, dst in g.flows)
    n_out = Counter(src for src, _ in g.flows)
    assert not [i for i in g.gateways() if n_in[i] == 1 and n_out[i] == 1]


def test_task_ids_match_between_the_file_and_the_explicit_model(written) -> None:
    t = SHAPES["optional-repeats"]
    explicit = {n.get_id() for n in to_bpmn(t).get_nodes() if type(n).__name__ == "Task"}
    assert set(written(t).tasks()) == explicit == {"task_a", "task_b"}


def test_marker_comes_after_the_tasks_flow_references(tmp_path: Path) -> None:
    # BPMN's schema orders a task's children; bpmn-js reads the marker either
    # way, but a strict tool would reject a marker before incoming/outgoing.
    write_bpmn(node("sequence", A, repeat(B)), tmp_path / "t.bpmn")
    process = next(ET.parse(tmp_path / "t.bpmn").getroot().iter(f"{BPMN_NS}process"))
    task = next(e for e in process if e.attrib.get("id") == "task_b")
    assert list(task)[-1].tag == f"{BPMN_NS}standardLoopCharacteristics"


# --- layout -------------------------------------------------------------------

needs_graphviz = pytest.mark.skipif(not pt.graphviz_available(), reason="needs Graphviz dot")


def drawn(path: Path) -> tuple[dict[str, tuple[float, ...]], dict[str, list[tuple[float, float]]]]:
    """Shape bounds (x, y, width, height) and flow waypoints of a written file."""
    root = ET.parse(path).getroot()
    shapes = {
        s.attrib["bpmnElement"]: tuple(
            float(s.find(f"{DC_NS}Bounds").attrib[k]) for k in ("x", "y", "width", "height")
        )
        for s in root.iter(f"{BPMNDI_NS}BPMNShape")
    }
    flows = {
        e.attrib["bpmnElement"].removeprefix("id"): [
            (float(w.attrib["x"]), float(w.attrib["y"])) for w in e.iter(f"{DI_NS}waypoint")
        ]
        for e in root.iter(f"{BPMNDI_NS}BPMNEdge")
    }
    return shapes, flows


@needs_graphviz
def test_every_task_has_room_for_two_lines_above_a_marker(tmp_path: Path) -> None:
    write_bpmn(SHAPES["optional-repeats"], tmp_path / "t.bpmn")
    shapes, _ = drawn(tmp_path / "t.bpmn")
    assert shapes["task_a"][2:] == pt.TASK_SIZE
    assert shapes["task_b"][2:] == pt.TASK_SIZE


@needs_graphviz
@pytest.mark.parametrize("t", SHAPES.values(), ids=SHAPES.keys())
def test_flows_end_on_the_gateway_outline(t: TreeNode, tmp_path: Path) -> None:
    # pm4py's layouter clipped flows to the bounding square, so arrows stopped
    # short of the diamond's edges.
    write_bpmn(t, tmp_path / "t.bpmn")
    shapes, flows = drawn(tmp_path / "t.bpmn")

    def diamond_distance(point: tuple[float, float], gateway: str) -> float:
        x, y, width, height = shapes[gateway]
        half_w, half_h = width / 2, height / 2
        return abs(point[0] - x - half_w) / half_w + abs(point[1] - y - half_h) / half_h

    for flow, points in flows.items():
        _, source, target = flow.split("__")
        if source.startswith("gw_"):
            assert diamond_distance(points[0], source) == pytest.approx(1, abs=0.12), flow
        if target.startswith("gw_"):
            assert diamond_distance(points[-1], target) == pytest.approx(1, abs=0.12), flow


@needs_graphviz
def test_shapes_do_not_overlap(tmp_path: Path) -> None:
    write_bpmn(SHAPES["optional-repeats"], tmp_path / "t.bpmn")
    shapes, _ = drawn(tmp_path / "t.bpmn")
    for (_, a), (_, b) in itertools.combinations(shapes.items(), 2):
        apart = a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
        assert apart or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]


# --- redrawing saved runs -------------------------------------------------------


@pytest.fixture
def redraw():
    path = Path(__file__).parent.parent / "scripts" / "redraw_bpmn.py"
    spec = importlib.util.spec_from_file_location("redraw_bpmn", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """One saved run: a tree and the older, wordier drawing of a repeated task."""
    segment = tmp_path / "run" / "seg"
    segment.mkdir(parents=True)
    tree = node("sequence", A, repeat(B))
    save_tree(tree, segment / "as_is_tree.json")
    write_bpmn(tree, segment / "as_is.bpmn", process_id="as_is_seg")
    return tmp_path


def test_redraw_replaces_a_drawing_with_the_same_tasks(redraw, run_dir: Path) -> None:
    saved = run_dir / "run" / "seg" / "as_is.bpmn"
    saved.write_text(saved.read_text(encoding="utf-8") + "<!-- old -->", encoding="utf-8")
    assert redraw.tasks_of(saved) == {("task_a", "A"), ("task_b", "B")}
    assert redraw.redraw_runs(run_dir) == (1, 0, [])
    assert "<!-- old -->" not in saved.read_text(encoding="utf-8")


def test_redraw_leaves_a_drawing_with_other_tasks_alone(redraw, run_dir: Path) -> None:
    saved = run_dir / "run" / "seg" / "as_is.bpmn"
    tampered = saved.read_text(encoding="utf-8").replace('id="task_b"', 'id="task_other"')
    saved.write_text(tampered, encoding="utf-8")
    count, _, refused = redraw.redraw_runs(run_dir)
    assert count == 0 and "different tasks" in refused[0]
    assert saved.read_text(encoding="utf-8") == tampered


def test_redraw_rebuilds_a_to_be_model_from_its_proposals(redraw, run_dir: Path) -> None:
    agent = run_dir / "run" / "seg" / "agent" / "a1"
    agent.mkdir(parents=True)
    tree = load_tree(run_dir / "run" / "seg" / "as_is_tree.json")
    proposal = {
        "title": "t",
        "heuristic_id": "H",
        "edit_operations": [{"op": "automate_activity", "activity": "A"}],
        "evidence_ids": ["E-1"],
        "rationale": "r",
        "expected_effect": "e",
        "risks": ["k"],
        "assumptions": ["a"],
    }
    ops = RedesignSet.model_validate({"proposals": [proposal, proposal]})
    (agent / "proposals_r0.json").write_text(ops.model_dump_json(), encoding="utf-8")
    to_be, _ = apply_proposal(tree, ops.proposals[0].edit_operations)
    write_to_be(to_be, agent, "to_be_r0_p1")
    stale = (agent / "to_be_r0_p1.bpmn").read_text(encoding="utf-8") + "<!-- old -->"
    (agent / "to_be_r0_p1.bpmn").write_text(stale, encoding="utf-8")

    assert redraw.redraw_runs(run_dir) == (1, 1, [])  # p2 has no file, so is skipped
    assert "<!-- old -->" not in (agent / "to_be_r0_p1.bpmn").read_text(encoding="utf-8")
