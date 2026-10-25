"""Validators, tools, prompts and config of the agent (pure code, no graph)."""

from __future__ import annotations

import pytest

from agent_fixtures import (
    ACTIVITIES,
    PROTECTED,
    SEGMENT,
    SLUG,
    evidence_items,
    valid_diagnosis,
    valid_redesign,
)
from processpilot.agent.catalogue import (
    DomainFact,
    DomainKnowledge,
    HeuristicCatalogue,
    Price,
    load_agent_settings,
    load_domain_knowledge,
    load_heuristics,
)
from processpilot.agent.llm import TokenUsage, bare_model_name, cost_usd, model_fingerprint
from processpilot.agent.prompts import render_digest
from processpilot.agent.state import (
    OPERATION_NAMES,
    AddActivity,
    Diagnosis,
    MergeActivities,
    RedesignSet,
    ReorderActivities,
    modified_activities,
)
from processpilot.agent.tools import EvidenceTools
from processpilot.agent.validation import (
    ValidationContext,
    extract_numbers,
    validate_diagnosis,
    validate_redesign,
)
from processpilot.mining.evidence import EvidenceStore


@pytest.fixture
def store() -> EvidenceStore:
    other = evidence_items(segment="Seg B", slug="seg-b")
    return EvidenceStore(items=[*evidence_items(), *other])


@pytest.fixture
def ctx(store: EvidenceStore) -> ValidationContext:
    return ValidationContext(
        segment=SEGMENT,
        store=EvidenceTools(store, SEGMENT).segment_store(),
        activities=frozenset(ACTIVITIES),
        catalogue=load_heuristics(),
        domain=DomainKnowledge(
            version=1, domain="test", facts=[DomainFact(id="D-01", statement="x")]
        ),
        protected_activities=frozenset(PROTECTED),
    )


def _codes(issues: list) -> list[str]:
    return [i.code for i in issues]


# --- diagnosis -----------------------------------------------------------------


def test_valid_diagnosis_has_no_issues(ctx: ValidationContext) -> None:
    assert validate_diagnosis(Diagnosis.model_validate(valid_diagnosis()), ctx) == []


def test_evidence_of_another_segment_counts_as_unknown(ctx: ValidationContext) -> None:
    d = valid_diagnosis()
    d["hypotheses"][0]["evidence_ids"] = ["BN-01-seg-b"]
    issues = validate_diagnosis(Diagnosis.model_validate(d), ctx)
    assert "unknown_evidence_id" in _codes(issues)


def test_unknown_domain_fact_is_flagged(ctx: ValidationContext) -> None:
    d = valid_diagnosis()
    d["hypotheses"][0]["domain_fact_ids"] = ["D-01", "D-77"]
    issues = validate_diagnosis(Diagnosis.model_validate(d), ctx)
    assert _codes(issues) == ["unknown_domain_fact_id"]
    assert "D-77" in issues[0].message


def test_diagnosis_schema_bounds() -> None:
    h = valid_diagnosis()["hypotheses"][0]
    with pytest.raises(ValueError):
        Diagnosis.model_validate({"hypotheses": []})
    with pytest.raises(ValueError):
        Diagnosis.model_validate({"hypotheses": [h] * 6})
    with pytest.raises(ValueError):
        Diagnosis.model_validate({"hypotheses": [{**h, "evidence_ids": []}]})


# --- numbers -------------------------------------------------------------------


def test_number_must_come_from_the_cited_evidence(ctx: ValidationContext) -> None:
    d = valid_diagnosis()
    # 29.3 days is real evidence, but in BN-02, which this hypothesis does not cite.
    d["hypotheses"][0]["description"] = "Payment waits 29.3 days [BN-01-seg-a]."
    d["hypotheses"][0]["evidence_ids"] = [f"BN-01-{SLUG}"]
    issues = validate_diagnosis(Diagnosis.model_validate(d), ctx)
    assert _codes(issues) == ["unsupported_number"]
    d["hypotheses"][0]["evidence_ids"] = [f"BN-02-{SLUG}"]
    assert validate_diagnosis(Diagnosis.model_validate(d), ctx) == []


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("median 29.3 days", True),
        ("about 29 days", True),  # 29.3 rounded at the written precision
        ("about 30 days", False),  # not a rounding of any cited number
        ("90.0% of cases", True),
        ("90% of cases", True),
        ("saves 50% of the wait", False),  # derived / invented
        ("P90 45.0 days", True),
        ("2-step improvement", True),  # compound adjective, not a quantity
    ],
)
def test_number_rounding_rule(ctx: ValidationContext, text: str, ok: bool) -> None:
    d = valid_diagnosis()
    d["hypotheses"][0]["description"] = text
    d["hypotheses"][0]["evidence_ids"] = [f"BN-02-{SLUG}"]
    issues = validate_diagnosis(Diagnosis.model_validate(d), ctx)
    assert (issues == []) is ok, issues


def test_numbers_inside_ids_and_activity_names_are_ignored() -> None:
    text = "See [BN-01-3-way-match] and H-TASK-COMPOSITION, user_602, R-02 on 'Step 2 Check'."
    assert extract_numbers(text, ["Step 2 Check"]) == []
    assert [v for v, _, _ in extract_numbers("1,044 cases and 19.9%", [])] == [1044.0, 19.9]


def test_numbers_in_alternative_explanations_are_checked(ctx: ValidationContext) -> None:
    d = valid_diagnosis()
    d["hypotheses"][0]["alternative_explanations"] = ["Payment terms of 60 days."]
    issues = validate_diagnosis(Diagnosis.model_validate(d), ctx)
    assert _codes(issues) == ["unsupported_number"]
    assert issues[0].path == "hypotheses[0].alternative_explanations[0]"


# --- redesign ------------------------------------------------------------------


def test_valid_redesign_has_no_issues(ctx: ValidationContext) -> None:
    assert validate_redesign(RedesignSet.model_validate(valid_redesign()), ctx) == []


def test_heuristic_must_exist_and_be_editable(ctx: ValidationContext) -> None:
    r = valid_redesign()
    r["proposals"][0]["heuristic_id"] = "H-MAGIC"
    r["proposals"][1]["heuristic_id"] = "H-EXTRA-RESOURCES"
    issues = validate_redesign(RedesignSet.model_validate(r), ctx)
    assert _codes(issues) == ["unknown_heuristic", "heuristic_not_editable"]


@pytest.mark.parametrize(
    ("op", "codes"),
    [
        ({"op": "remove_activity", "activity": "Pay Invoice"}, ["protected_activity"]),
        ({"op": "remove_activity", "activity": "Teleport Goods"}, ["unknown_activity"]),
        (
            {"op": "parallelize_activities", "activities": ["Record Invoice", "Pay Invoice"]},
            ["protected_activity"],
        ),
        (
            {
                "op": "merge_activities",
                "activities": ["Pay Invoice", "Record Invoice"],
                "merged_name": "Pay",
            },
            ["protected_activity"],
        ),
        (
            {"op": "reorder_activities", "activity": "Pay Invoice", "after": "Create Order"},
            ["protected_activity"],
        ),
        # Anchoring on a protected activity leaves it intact: allowed.
        ({"op": "add_activity", "name": "Archive", "after": "Pay Invoice"}, []),
        (
            {"op": "reorder_activities", "activity": "Record Invoice", "after": "Pay Invoice"},
            [],
        ),
        (
            {"op": "add_activity", "name": "Receive Goods", "after": "Create Order"},
            ["activity_already_exists"],
        ),
        (
            {
                "op": "merge_activities",
                "activities": ["Create Order", "Approve Order"],
                "merged_name": "Receive Goods",
            },
            ["activity_already_exists"],
        ),
        (
            {
                "op": "merge_activities",
                "activities": ["Create Order", "Approve Order"],
                "merged_name": "Create Order",
            },
            [],
        ),
    ],
)
def test_edit_operation_checks(ctx: ValidationContext, op: dict, codes: list[str]) -> None:
    r = valid_redesign()
    r["proposals"][0]["edit_operations"] = [op]
    assert _codes(validate_redesign(RedesignSet.model_validate(r), ctx)) == codes


def test_redesign_schema_bounds() -> None:
    p = valid_redesign()["proposals"][0]
    with pytest.raises(ValueError):
        RedesignSet.model_validate({"proposals": [p]})
    with pytest.raises(ValueError):
        RedesignSet.model_validate({"proposals": [p] * 4})
    with pytest.raises(ValueError):
        RedesignSet.model_validate({"proposals": [{**p, "edit_operations": []}] * 2})
    with pytest.raises(ValueError):
        RedesignSet.model_validate(
            {"proposals": [{**p, "edit_operations": [{"op": "teleport"}]}] * 2}
        )


def test_modified_activities_excludes_anchors() -> None:
    assert modified_activities(AddActivity(name="X", after="A")) == []
    assert modified_activities(ReorderActivities(activity="B", after="A")) == ["B"]
    assert modified_activities(MergeActivities(activities=["A", "B"], merged_name="AB")) == [
        "A",
        "B",
    ]


# --- tools and prompts -----------------------------------------------------------


def test_tools_are_segment_scoped_and_return_copies(store: EvidenceStore) -> None:
    tools = EvidenceTools(store, SEGMENT)
    assert [i.id for i in tools.get_top_bottlenecks(1)] == [f"BN-01-{SLUG}"]
    assert len(tools.get_top_bottlenecks()) == 2
    assert all(i.segment == SEGMENT for i in tools.all_items())
    with pytest.raises(KeyError, match="belongs to segment"):
        tools.get_evidence("BN-01-seg-b")
    item = tools.get_evidence(f"BN-01-{SLUG}")
    item.metrics["rank"] = 999
    assert store.get(f"BN-01-{SLUG}").metrics["rank"] == 1
    with pytest.raises(KeyError):
        EvidenceTools(store, "No Such Segment")


def test_digest_notes_missing_evidence_types() -> None:
    only_kpi = [i for i in evidence_items() if i.type == "kpi"]
    digest = render_digest(EvidenceTools(EvidenceStore(items=only_kpi), SEGMENT))
    assert "NOT evidence that the segment is compliant" in digest
    assert "No rework items" in digest


# --- configuration -----------------------------------------------------------------


def test_repo_heuristics_catalogue_is_valid() -> None:
    cat = load_heuristics()
    assert len(cat.ids()) == len(cat.heuristics)  # unique ids
    for h in cat.editable():
        assert h.typical_operations, h.id
        assert set(h.typical_operations) <= set(OPERATION_NAMES)
    assert "H-EXTRA-RESOURCES" in cat.ids()
    assert "H-EXTRA-RESOURCES" not in {h.id for h in cat.editable()}


def test_duplicate_heuristic_or_fact_ids_fail_at_load() -> None:
    raw = load_heuristics().model_dump()
    raw["heuristics"].append(raw["heuristics"][0])
    with pytest.raises(ValueError, match="duplicate heuristic"):
        HeuristicCatalogue.model_validate(raw)
    fact = DomainFact(id="D-01", statement="x")
    with pytest.raises(ValueError, match="duplicate domain fact"):
        DomainKnowledge(version=1, domain="d", facts=[fact, fact])


def test_repo_domain_knowledge_and_agent_settings_load() -> None:
    p2p = load_domain_knowledge("p2p")
    assert p2p is not None and 1 <= len(p2p.facts) <= 15
    assert len(p2p.ids()) == len(p2p.facts)
    assert load_domain_knowledge(None) is None
    settings = load_agent_settings()
    assert settings.max_retries == 2 and settings.max_revision_loops == 2
    assert settings.model.structured_output_method == "json_schema"


def test_model_kwargs_are_per_provider_and_unknown_provider_fails() -> None:
    # ChatOllama silently ignores Anthropic kwargs, so a shared dict would run
    # a local model with Ollama's default (too small) context window.
    model = load_agent_settings().model
    assert model.kwargs_for("anthropic:claude-opus-5-5")["effort"] == "high"
    local = model.kwargs_for("ollama:qwen3:4b-instruct-2507-q4_K_M")
    assert local["num_ctx"] >= 8192 and local["reasoning"] is False
    assert "effort" not in local
    with pytest.raises(ValueError, match="no model.init_kwargs.openai"):
        model.kwargs_for("openai:gpt-4.1")
    assert model_fingerprint("anthropic:claude-opus-5-5") is None


def test_cost_accounts_for_cache_tokens() -> None:
    price = Price(input=4.0, output=20.0, cache_read=0.2, cache_write=5.0)
    usage = TokenUsage(
        input_tokens=10_000, output_tokens=1_000, cache_read_tokens=6_000, cache_creation_tokens=0
    )
    assert cost_usd(usage, price) == pytest.approx((4_000 * 4 + 6_000 * 0.2 + 1_000 * 20) / 1e6)
    assert cost_usd(usage, None) is None
    assert cost_usd(TokenUsage(), price) is None
    assert bare_model_name("anthropic:claude-opus-5-5") == "claude-opus-5-5"


# --- trial application on the as-is model (Phase 4) ------------------------------


def test_inapplicable_edit_is_reported_for_retry(ctx: ValidationContext) -> None:
    from dataclasses import replace

    from agent_fixtures import as_is_tree

    with_model = replace(ctx, as_is_tree=as_is_tree())
    redesign = valid_redesign()
    # Create Order and Receive Goods are not adjacent: Approve Order is between them.
    redesign["proposals"][1]["edit_operations"] = [
        {"op": "parallelize_activities", "activities": ["Create Order", "Receive Goods"]}
    ]
    issues = validate_redesign(RedesignSet.model_validate(redesign), with_model)
    assert _codes(issues) == ["edit_not_applicable"]
    assert issues[0].path == "proposals[1].edit_operations[0]"
    assert "between them: Approve Order" in issues[0].message
    # Without a model the trial run is skipped (runs mined before Phase 4).
    assert validate_redesign(RedesignSet.model_validate(redesign), ctx) == []


def test_rare_activity_outside_the_model_is_explained(ctx: ValidationContext) -> None:
    from dataclasses import replace

    rare = replace(ctx, observed_activities=frozenset({*ACTIVITIES, "Rare Step"}))
    redesign = valid_redesign()
    redesign["proposals"][0]["edit_operations"] = [
        {"op": "remove_activity", "activity": "Rare Step"}
    ]
    issues = validate_redesign(RedesignSet.model_validate(redesign), rare)
    assert _codes(issues) == ["unknown_activity"]
    assert "too rarely to be part of the model" in issues[0].message


def test_new_operations_are_validated(ctx: ValidationContext) -> None:
    redesign = valid_redesign()
    redesign["proposals"][0]["edit_operations"] = [
        {"op": "add_check", "name": "Approve Order", "after": "Create Order"},
        {"op": "automate_activity", "activity": "Pay Invoice"},  # protected: allowed
    ]
    issues = validate_redesign(RedesignSet.model_validate(redesign), ctx)
    assert _codes(issues) == ["activity_already_exists"]
