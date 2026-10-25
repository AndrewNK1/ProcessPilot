"""Typed loaders for the agent's non-evidence inputs.

WHY typed: the heuristics catalogue and domain facts are the only knowledge
the LLM may use besides evidence, and the validator checks LLM output against
them. A typo in a YAML key (e.g. an unknown operation name) must fail at load
time, not let a proposal through later.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from processpilot.agent.state import OPERATION_NAMES
from processpilot.config import load_config_yaml


class Applicability(BaseModel):
    """One evidence signal that makes a heuristic worth considering."""

    evidence_type: Literal[
        "bottleneck", "activity", "variant", "rework", "resource", "conformance", "kpi"
    ]
    signal: str


class Heuristic(BaseModel):
    """One redesign best practice from the catalogue."""

    id: str
    name: str
    description: str
    when_applicable: list[Applicability] = Field(min_length=1)
    typical_operations: list[str]
    risks: str
    bpmn_editable: bool = True
    confidence: Literal["established", "uncertain"]

    @field_validator("typical_operations")
    @classmethod
    def _known_operations(cls, ops: list[str]) -> list[str]:
        unknown = [o for o in ops if o not in OPERATION_NAMES]
        if unknown:
            raise ValueError(f"unknown edit operations {unknown}; allowed: {OPERATION_NAMES}")
        return ops


class HeuristicCatalogue(BaseModel):
    """The closed set of heuristics the redesign step may select from."""

    version: int
    heuristics: list[Heuristic]

    def ids(self) -> set[str]:
        """Every heuristic id, editable or not."""
        return {h.id for h in self.heuristics}

    def editable(self) -> list[Heuristic]:
        """Heuristics expressible as BPMN edits, i.e. selectable by redesign."""
        return [h for h in self.heuristics if h.bpmn_editable]


class DomainFact(BaseModel):
    """One piece of general domain knowledge (not a finding)."""

    id: str
    statement: str


class DomainKnowledge(BaseModel):
    """A dataset's domain-fact file (``configs/domain/<name>.yaml``)."""

    version: int
    domain: str
    facts: list[DomainFact] = Field(max_length=15)

    def ids(self) -> set[str]:
        """Every fact id."""
        return {f.id for f in self.facts}


class ModelSettings(BaseModel):
    """How the chat model is created; the id itself comes from ``env_var``.

    WHY keyed by provider: ``init_chat_model`` forwards kwargs to the
    provider's chat class, and some classes silently ignore keys they do not
    know (ChatOllama drops ``effort``/``max_tokens``). A shared kwargs dict
    would therefore run a local model with Ollama's default context window,
    which truncates the evidence digest without any error.
    """

    env_var: str
    init_kwargs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    structured_output_method: str

    def kwargs_for(self, model_id: str) -> dict[str, Any]:
        """The kwargs for ``"<provider>:<model>"``; unknown provider fails loudly."""
        provider = model_id.split(":", 1)[0]
        if provider not in self.init_kwargs:
            raise ValueError(
                f"no model.init_kwargs.{provider} in configs/agent.yaml for {model_id!r}; "
                f"configured providers: {sorted(self.init_kwargs)}"
            )
        return self.init_kwargs[provider]


class Price(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


class AgentSettings(BaseModel):
    """``configs/agent.yaml``."""

    model: ModelSettings
    max_retries: int = Field(ge=0)
    max_revision_loops: int = Field(ge=0)
    pricing_usd_per_mtok: dict[str, Price] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, raw: dict[str, Any]) -> AgentSettings:
        """Flatten the YAML's grouping into the model."""
        return cls(
            model=raw["model"],
            max_retries=raw["validation"]["max_retries"],
            max_revision_loops=raw["review"]["max_revision_loops"],
            pricing_usd_per_mtok=raw.get("pricing_usd_per_mtok") or {},
        )


def load_heuristics() -> HeuristicCatalogue:
    """``configs/heuristics.yaml``."""
    return HeuristicCatalogue.model_validate(load_config_yaml("heuristics.yaml"))


def load_domain_knowledge(name: str | None) -> DomainKnowledge | None:
    """``configs/domain/<name>.yaml``, or ``None`` if the preset names none."""
    if not name:
        return None
    return DomainKnowledge.model_validate(load_config_yaml("domain", f"{name}.yaml"))


def load_agent_settings() -> AgentSettings:
    """``configs/agent.yaml``."""
    return AgentSettings.from_yaml(load_config_yaml("agent.yaml"))
