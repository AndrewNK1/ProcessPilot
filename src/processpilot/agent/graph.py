"""Graph assembly, checkpointing and the per-segment runner.

WHY a checkpointer is mandatory here: ``human_review`` uses LangGraph's
``interrupt()``, which can only resume if the state was persisted. The analyst
may approve hours later, possibly from the Streamlit app rather than the
notebook that started the run. The SQLite file lives at
``outputs/runs/<run_id>/checkpoints.sqlite``; each segment run is its own
thread, ``<segment-slug>:<agent_run_id>``, so ``(segment, agent_run_id)`` is
all a later process needs to resume.

The checkpointer only deserialises the agent's own state classes
(``CHECKPOINT_TYPES``), not arbitrary importable types.

Every run writes inputs, prompts, raw model outputs, model id, library versions
and timestamps to ``outputs/runs/<run_id>/<segment>/agent/<agent_run_id>/``
(see ``runlog.py``).
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from processpilot.agent import nodes
from processpilot.agent.catalogue import (
    AgentSettings,
    DomainKnowledge,
    HeuristicCatalogue,
    load_agent_settings,
    load_domain_knowledge,
    load_heuristics,
)
from processpilot.agent.llm import LangChainLLM, StructuredLLM
from processpilot.agent.nodes import AgentContext
from processpilot.agent.runlog import RunLogger
from processpilot.agent.state import CHECKPOINT_TYPES, AgentState, Constraints, ReviewDecision
from processpilot.agent.tools import EvidenceTools
from processpilot.mining.evidence import EvidenceStore, segment_slug
from processpilot.mining.run import AS_IS_TREE_FILE
from processpilot.process_tree import TreeNode, labels
from processpilot.redesign.bpmn_edit import load_tree

CHECKPOINT_FILE = "checkpoints.sqlite"

NodeCallback = Callable[[str], None]


def build_graph(ctx: AgentContext, checkpointer: Any = None) -> CompiledStateGraph:
    """The fixed pipeline for one segment run; ``ctx`` is bound into every node."""

    def bind(fn: Any) -> Any:
        def node(state: AgentState) -> dict[str, Any]:
            return fn(state, ctx)

        node.__name__ = fn.__name__
        return node

    g = StateGraph(AgentState)
    for fn in (
        nodes.load_evidence,
        nodes.diagnose,
        nodes.redesign,
        nodes.apply_edits,
        nodes.validate,
        nodes.human_review,
        nodes.finalize,
    ):
        g.add_node(fn.__name__, bind(fn))
    g.add_edge(START, "load_evidence")
    g.add_edge("load_evidence", "diagnose")
    g.add_conditional_edges(
        "diagnose", nodes.route_after_llm, {"ok": "redesign", "failed": "finalize"}
    )
    g.add_conditional_edges(
        "redesign", nodes.route_after_llm, {"ok": "apply_edits", "failed": "finalize"}
    )
    g.add_edge("apply_edits", "validate")
    g.add_edge("validate", "human_review")
    g.add_conditional_edges(
        "human_review",
        nodes.route_after_review,
        {"redesign": "redesign", "finalize": "finalize"},
    )
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def open_checkpointer(path: Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    """SQLite checkpointer restricted to the agent's state types."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    serde = JsonPlusSerializer(allowed_msgpack_modules=None).with_msgpack_allowlist(
        CHECKPOINT_TYPES
    )
    return SqliteSaver(conn, serde=serde), conn


def segment_activities(run_dir: Path, segment: str) -> list[str]:
    """Every activity observed in a segment, read from the mined ``dfg.json``.

    WHY dfg.json rather than the log: the agent layer never opens event data.
    The DFG lists every activity that occurs (as an edge endpoint or as a
    start/end activity).
    """
    dfg = json.loads((run_dir / segment_slug(segment) / "dfg.json").read_text(encoding="utf-8"))
    names = set(dfg["start_activities"]) | set(dfg["end_activities"])
    for e in dfg["edges"]:
        names.update((e["source"], e["target"]))
    return sorted(names)


def load_as_is_tree(run_dir: Path, segment: str) -> TreeNode | None:
    """The segment's mined process tree, or ``None`` for runs mined before Phase 4.

    WHY the tree and not the DFG decides what may be edited: the inductive
    miner can leave rare activities out of the model, and an edit can only
    change what the model contains.
    """
    path = run_dir / segment_slug(segment) / AS_IS_TREE_FILE
    return load_tree(path) if path.is_file() else None


def model_id_from_env(settings: AgentSettings) -> str:
    """The model id from ``settings.model.env_var``, with a setup hint if unset."""
    var = settings.model.env_var
    model_id = os.environ.get(var, "").strip()
    if not model_id:
        raise OSError(
            f"environment variable {var} is not set; set it in .env (see .env.example), "
            "e.g. anthropic:claude-opus-5-5, and call config.load_env() first"
        )
    return model_id


def create_llm(model_id: str, settings: AgentSettings, run_dir: Path) -> StructuredLLM:
    """The model behind ``model_id``; ``mock`` is the rule-based stand-in.

    Anything else is a ``provider:model`` id for ``init_chat_model``.
    """
    from processpilot.llm.mock import MOCK_MODEL_ID, MockLLM

    if model_id == MOCK_MODEL_ID:
        return MockLLM(run_dir)
    return LangChainLLM(model_id, settings.model)


def _new_agent_run_id(segment_dir: Path) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate, n = stamp, 1
    while (segment_dir / "agent" / candidate).exists():
        n += 1
        candidate = f"{stamp}-{n}"
    return candidate


class AgentRunner:
    """Starts, inspects and resumes per-segment agent runs of one mining run.

    Holds one SQLite connection; use as a context manager or call ``close``.
    A new runner over the same ``run_dir`` can resume any paused run.
    """

    def __init__(
        self,
        run_dir: Path,
        preset: dict[str, Any],
        llm: StructuredLLM | None = None,
        settings: AgentSettings | None = None,
        catalogue: HeuristicCatalogue | None = None,
        domain: DomainKnowledge | None = None,
    ) -> None:
        """Load evidence and configuration; the model is created lazily."""
        self.run_dir = Path(run_dir)
        self.run_id = self.run_dir.name
        self.preset = preset
        self.settings = settings or load_agent_settings()
        self.catalogue = catalogue or load_heuristics()
        self.domain = domain or load_domain_knowledge(preset.get("domain_knowledge"))
        self.store = EvidenceStore.from_json(self.run_dir / "evidence.json")
        self.llm = llm or create_llm(model_id_from_env(self.settings), self.settings, self.run_dir)
        self._saver, self._conn = open_checkpointer(self.run_dir / CHECKPOINT_FILE)

    def __enter__(self) -> AgentRunner:
        """Context-manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the SQLite connection."""
        self.close()

    def close(self) -> None:
        """Release the checkpoint database."""
        self._conn.close()

    def default_constraints(self) -> Constraints:
        """Constraints from the preset's ``redesign`` block."""
        block = self.preset.get("redesign") or {}
        return Constraints(
            text=block.get("constraints_text") or [],
            protected_activities=block.get("protected_activities") or [],
        )

    def _context(self, segment: str, agent_run_id: str) -> AgentContext:
        seg_dir = self.run_dir / segment_slug(segment)
        observed = segment_activities(self.run_dir, segment)
        tree = load_as_is_tree(self.run_dir, segment)
        return AgentContext(
            run_id=self.run_id,
            segment=segment,
            tools=EvidenceTools(self.store, segment),
            activities=sorted(labels(tree)) if tree is not None else observed,
            catalogue=self.catalogue,
            domain=self.domain,
            settings=self.settings,
            llm=self.llm,
            logger=RunLogger(seg_dir / "agent" / agent_run_id),
            segment_dir=seg_dir,
            as_is_tree=tree,
            observed_activities=observed,
        )

    def _graph(self, segment: str, agent_run_id: str) -> CompiledStateGraph:
        return build_graph(self._context(segment, agent_run_id), self._saver)

    @staticmethod
    def _config(segment: str, agent_run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": f"{segment_slug(segment)}:{agent_run_id}"}}

    def new_agent_run_id(self, segment: str) -> str:
        """A fresh, unused agent run id for ``segment`` (for callers that index it first)."""
        return _new_agent_run_id(self.run_dir / segment_slug(segment))

    def _drive(
        self, segment: str, agent_run_id: str, payload: Any, on_node: NodeCallback | None
    ) -> None:
        """Run the graph until it pauses or ends, reporting each finished node.

        ``stream`` instead of ``invoke`` so a caller (the API's job) can show
        progress during a multi-minute run; both execute the graph the same way.
        """
        graph = self._graph(segment, agent_run_id)
        for chunk in graph.stream(
            payload, self._config(segment, agent_run_id), stream_mode="updates"
        ):
            if on_node is not None:
                for name in chunk:
                    if not name.startswith("__"):
                        on_node(name)

    def start(
        self,
        segment: str,
        constraints: Constraints | None = None,
        agent_run_id: str | None = None,
        on_node: NodeCallback | None = None,
    ) -> tuple[str, AgentState]:
        """Run until the review interrupt (or failure). Returns (agent_run_id, state)."""
        agent_run_id = agent_run_id or self.new_agent_run_id(segment)
        initial = AgentState(
            run_id=self.run_id,
            agent_run_id=agent_run_id,
            segment=segment,
            constraints=constraints or self.default_constraints(),
        )
        self._drive(segment, agent_run_id, initial, on_node)
        return agent_run_id, self.state(segment, agent_run_id)

    def resume(
        self,
        segment: str,
        agent_run_id: str,
        decision: ReviewDecision | dict[str, Any],
        on_node: NodeCallback | None = None,
    ) -> AgentState:
        """Resume a paused run with the analyst's decision."""
        payload = ReviewDecision.model_validate(
            decision.model_dump() if isinstance(decision, ReviewDecision) else decision
        ).model_dump()
        if not self.pending_review(segment, agent_run_id):
            raise RuntimeError(f"run {agent_run_id} of {segment!r} is not awaiting review")
        self._drive(segment, agent_run_id, Command(resume=payload), on_node)
        return self.state(segment, agent_run_id)

    def state(self, segment: str, agent_run_id: str) -> AgentState:
        """Current checkpointed state of a run."""
        snapshot = self._graph(segment, agent_run_id).get_state(self._config(segment, agent_run_id))
        if not snapshot.values:
            raise KeyError(f"no checkpoint for run {agent_run_id} of {segment!r}")
        values = snapshot.values
        return values if isinstance(values, AgentState) else AgentState.model_validate(values)

    def pending_review(self, segment: str, agent_run_id: str) -> dict[str, Any] | None:
        """The interrupt payload if the run is paused at ``human_review``."""
        snapshot = self._graph(segment, agent_run_id).get_state(self._config(segment, agent_run_id))
        for intr in snapshot.interrupts:
            return intr.value
        return None
