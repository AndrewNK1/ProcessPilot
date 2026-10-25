"""End-to-end agent graph tests with a scripted fake model (no real LLM)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_fixtures import (
    SEGMENT,
    SLUG,
    ParseFailure,
    ScriptedLLM,
    agent_settings,
    preset,
    valid_diagnosis,
    valid_redesign,
    write_run_dir,
)
from processpilot.agent.graph import AgentRunner
from processpilot.agent.runlog import RunLogger


def _runner(run_dir: Path, outputs: list, **settings_kw: int) -> tuple[AgentRunner, ScriptedLLM]:
    llm = ScriptedLLM(outputs)
    return AgentRunner(run_dir, preset(), llm=llm, settings=agent_settings(**settings_kw)), llm


def _log(run_dir: Path, agent_run_id: str) -> RunLogger:
    return RunLogger(run_dir / SLUG / "agent" / agent_run_id)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return write_run_dir(tmp_path)


def test_valid_path_pauses_for_review_then_approves(run_dir: Path) -> None:
    runner, llm = _runner(run_dir, [valid_diagnosis(), valid_redesign()])
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
        assert state.status == "awaiting_review"
        assert len(state.proposals.proposals) == 2
        assert [r.status for r in state.edit_results] == ["applied"] * 2
        assert [r.status for r in state.validation_results] == ["validated"] * 2
        for edit in state.edit_results:
            assert Path(edit.to_be_bpmn_path).is_file()
        assert state.edit_results[0].diff.added == ["Create and Approve Order"]
        # Answer key (agent_fixtures.case_waits): parallelizing Receive Goods and
        # Record Invoice saves the shorter wait (2 h) in both cases:
        # throughput 17/31 h -> 15/29 h, median 24 h -> 22 h.
        merge_est, par_est = (v.estimate for v in state.validation_results)
        assert merge_est.median_change_seconds == 0 and merge_est.not_estimated
        assert par_est.as_is.median_seconds == 24 * 3600
        assert par_est.to_be.median_seconds == 22 * 3600
        assert par_est.cases_affected == 2
        assert all(v.soundness.sound for v in state.validation_results)
        assert {r.id for r in state.evidence_index} >= {f"BN-01-{SLUG}", f"KPI-01-{SLUG}"}

        payload = runner.pending_review(SEGMENT, agent_run_id)
        assert payload["round"] == 1
        assert payload["proposals"]["proposals"][0]["heuristic_id"] == "H-TASK-COMPOSITION"

        final = runner.resume(SEGMENT, agent_run_id, {"decision": "approve", "comment": None})
    assert final.status == "approved"
    assert len(llm.calls) == 2

    log = _log(run_dir, agent_run_id)
    for name in ("inputs.json", "diagnosis.json", "proposals_r0.json", "summary.json"):
        assert (log.directory / name).is_file(), name
    summary = json.loads((log.directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "approved"
    assert summary["usage"]["llm_calls"] == 2
    # 2 calls x (1000 in x $4 + 200 out x $20) / 1e6
    assert summary["usage"]["cost_usd"] == pytest.approx(2 * 0.008)
    inputs = json.loads((log.directory / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["prompt_version"] and inputs["model_id"] == ScriptedLLM.model_id
    assert inputs["constraints"]["protected_activities"] == ["Pay Invoice"]


def test_prompts_contain_evidence_but_no_metrics_dump(run_dir: Path) -> None:
    runner, llm = _runner(run_dir, [valid_diagnosis(), valid_redesign()])
    with runner:
        runner.start(SEGMENT)
    diagnose_user = llm.calls[0][0][1].content
    assert f"[BN-02-{SLUG}]" in diagnose_user
    assert "path: Create Order -> Approve Order" in diagnose_user
    assert "example_case_ids" not in diagnose_user
    redesign_user = llm.calls[1][0][1].content
    assert "H-EXTRA-RESOURCES" not in redesign_user  # not BPMN-editable
    assert "Pay Invoice" in redesign_user.split("Protected activities")[1]


def test_unknown_evidence_id_is_retried_with_feedback(run_dir: Path) -> None:
    bad = valid_diagnosis()
    bad["hypotheses"][0]["evidence_ids"] = ["BN-99-seg-a"]
    runner, llm = _runner(run_dir, [bad, valid_diagnosis(), valid_redesign()])
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
    assert state.status == "awaiting_review"
    assert len(llm.calls) == 3
    retry_turns = llm.calls[1][0]
    assert retry_turns[-2].role == "assistant"
    assert "BN-99-seg-a" in retry_turns[-1].content
    assert "unknown_evidence_id" in retry_turns[-1].content
    kinds = [e["kind"] for e in _log(run_dir, agent_run_id).events()]
    assert kinds.count("retry") == 1


def test_unknown_evidence_id_three_times_fails_cleanly(run_dir: Path) -> None:
    bad = valid_diagnosis()
    bad["hypotheses"][0]["evidence_ids"] = ["BN-99-seg-a"]
    runner, llm = _runner(run_dir, [bad, bad, bad])
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
        assert runner.pending_review(SEGMENT, agent_run_id) is None
    assert state.status == "failed"
    assert "unknown_evidence_id" in state.error
    assert state.diagnosis is None and state.proposals is None
    assert len(llm.calls) == 3  # 1 + max_retries; redesign never ran
    log = _log(run_dir, agent_run_id)
    summary = json.loads((log.directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert any(e["kind"] == "node_failed" for e in log.events())


def test_protected_activity_is_rejected_and_retried(run_dir: Path) -> None:
    bad = valid_redesign()
    bad["proposals"][1]["edit_operations"] = [{"op": "remove_activity", "activity": "Pay Invoice"}]
    runner, llm = _runner(run_dir, [valid_diagnosis(), bad, valid_redesign()])
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
    assert state.status == "awaiting_review"
    first_redesign = [e for e in _log(run_dir, agent_run_id).events() if e["kind"] == "llm_call"][1]
    assert [i["code"] for i in first_redesign["issues"]] == ["protected_activity"]


def test_protected_activity_every_time_fails_the_node(run_dir: Path) -> None:
    bad = valid_redesign()
    bad["proposals"][0]["edit_operations"] = [
        {"op": "reorder_activities", "activity": "Pay Invoice", "after": "Create Order"}
    ]
    runner, _ = _runner(run_dir, [valid_diagnosis(), bad, bad, bad])
    with runner:
        _, state = runner.start(SEGMENT)
    assert state.status == "failed"
    assert "protected_activity" in state.error
    assert state.diagnosis is not None  # the diagnosis that did pass is kept


def test_schema_errors_and_parse_failures_are_retried(run_dir: Path) -> None:
    no_alternatives = copy.deepcopy(valid_diagnosis())
    no_alternatives["hypotheses"][0]["alternative_explanations"] = []
    runner, llm = _runner(
        run_dir, [ParseFailure(), no_alternatives, valid_diagnosis(), valid_redesign()]
    )
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
    assert state.status == "awaiting_review"
    calls = [e for e in _log(run_dir, agent_run_id).events() if e["kind"] == "llm_call"]
    assert calls[0]["issues"][0]["code"] == "schema_error"
    assert calls[1]["issues"][0]["code"] == "schema_error"
    assert "alternative_explanations" in calls[1]["issues"][0]["path"]


def test_unsupported_numbers_persisting_become_warnings(run_dir: Path) -> None:
    invented = valid_diagnosis()
    invented["hypotheses"][0]["description"] += " Fixing it saves 42 days."
    runner, llm = _runner(run_dir, [invented, invented, invented, valid_redesign()])
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
    assert state.status == "awaiting_review"
    assert [w.code for w in state.warnings] == ["unsupported_number"]
    assert "'42'" in state.warnings[0].message
    assert any(e["kind"] == "accepted_with_warnings" for e in _log(run_dir, agent_run_id).events())


def test_model_error_fails_the_node_without_retry(run_dir: Path) -> None:
    runner, llm = _runner(run_dir, [RuntimeError("401 invalid x-api-key")])
    with runner:
        _, state = runner.start(SEGMENT)
    assert state.status == "failed"
    assert "401" in state.error
    assert len(llm.calls) == 1


def test_reject_with_comment_loops_back_and_stops_after_two_revisions(run_dir: Path) -> None:
    outputs = [valid_diagnosis(), valid_redesign(), valid_redesign(), valid_redesign()]
    runner, llm = _runner(run_dir, outputs)
    reject = {"decision": "reject", "comment": "Too risky for segregation of duties."}
    with runner:
        agent_run_id, _ = runner.start(SEGMENT)
        s1 = runner.resume(SEGMENT, agent_run_id, reject)
        assert (s1.status, s1.revision_count) == ("awaiting_review", 1)
        s2 = runner.resume(SEGMENT, agent_run_id, reject)
        assert (s2.status, s2.revision_count) == ("awaiting_review", 2)
        s3 = runner.resume(SEGMENT, agent_run_id, reject)
    assert s3.status == "rejected"
    assert s3.revision_count == 2
    assert len(s3.review_history) == 3
    assert len(llm.calls) == 4  # diagnose + 3 redesigns
    revision_prompt = llm.calls[2][0][1].content
    assert "Too risky for segregation of duties." in revision_prompt
    assert "Combine order creation and approval" in revision_prompt  # previous proposals shown
    log = _log(run_dir, agent_run_id).directory
    assert {"proposals_r0.json", "proposals_r1.json", "proposals_r2.json"} <= {
        p.name for p in log.iterdir()
    }


def test_reject_without_comment_finalizes_as_rejected(run_dir: Path) -> None:
    runner, llm = _runner(run_dir, [valid_diagnosis(), valid_redesign()])
    with runner:
        agent_run_id, _ = runner.start(SEGMENT)
        state = runner.resume(SEGMENT, agent_run_id, {"decision": "reject", "comment": "  "})
    assert state.status == "rejected"
    assert state.revision_count == 0
    assert len(llm.calls) == 2


def test_resume_from_checkpointer_in_a_new_runner(run_dir: Path) -> None:
    first, _ = _runner(run_dir, [valid_diagnosis(), valid_redesign()])
    with first:
        agent_run_id, _ = first.start(SEGMENT)
    # A different runner (new SQLite connection, new model object) resumes it,
    # as the web app would; no model call is needed to approve.
    second, llm2 = _runner(run_dir, [])
    with second:
        assert second.state(SEGMENT, agent_run_id).status == "awaiting_review"
        final = second.resume(SEGMENT, agent_run_id, {"decision": "approve"})
    assert final.status == "approved"
    assert final.proposals is not None and len(final.proposals.proposals) == 2
    assert llm2.calls == []
    assert (run_dir / "checkpoints.sqlite").is_file()


def test_resume_rejects_invalid_payload_and_finished_runs(run_dir: Path) -> None:
    runner, _ = _runner(run_dir, [valid_diagnosis(), valid_redesign()])
    with runner:
        agent_run_id, _ = runner.start(SEGMENT)
        with pytest.raises(ValidationError):
            runner.resume(SEGMENT, agent_run_id, {"decision": "maybe"})
        runner.resume(SEGMENT, agent_run_id, {"decision": "approve"})
        with pytest.raises(RuntimeError, match="not awaiting review"):
            runner.resume(SEGMENT, agent_run_id, {"decision": "approve"})


def test_two_runs_of_one_segment_get_separate_threads_and_logs(run_dir: Path) -> None:
    runner, _ = _runner(
        run_dir, [valid_diagnosis(), valid_redesign(), valid_diagnosis(), valid_redesign()]
    )
    with runner:
        a, _ = runner.start(SEGMENT)
        b, _ = runner.start(SEGMENT)
        assert a != b
        runner.resume(SEGMENT, a, {"decision": "approve"})
        assert runner.state(SEGMENT, b).status == "awaiting_review"


def test_report_views_render_a_logged_run(run_dir: Path) -> None:
    from processpilot.agent.report import (
        diagnosis_markdown,
        issue_details,
        outcomes_markdown,
        proposals_markdown,
        validation_log,
        warnings_markdown,
    )

    bad = valid_diagnosis()
    bad["hypotheses"][0]["evidence_ids"] = ["BN-99-seg-a"]
    runner, _ = _runner(run_dir, [bad, valid_diagnosis(), valid_redesign()])
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
    log = _log(run_dir, agent_run_id)
    assert "H1 (medium confidence)" in diagnosis_markdown(state.diagnosis)
    md = proposals_markdown(state.proposals)
    assert "merge 'Create Order', 'Approve Order' into 'Create and Approve Order'" in md
    assert "run 'Receive Goods', 'Record Invoice' in parallel" in md
    outcomes = outcomes_markdown(state.edit_results, state.validation_results)
    assert "P1: edits applied" in outcomes and "added: Create and Approve Order" in outcomes
    assert "**ESTIMATE** median throughput 1.0 days -> 22.0 h (-8.3%)" in outcomes
    assert "not estimated: merge 'Create Order', 'Approve Order'" in outcomes
    assert warnings_markdown(state.warnings) == "**Warnings:** none."
    rows = validation_log(log)
    # With its only cited id unknown, the hypothesis's two numbers are unsupported too.
    assert [(r["node"], r["attempt"], r["issues"]) for r in rows] == [
        ("diagnose", 0, 3),
        ("diagnose", 1, 0),
        ("redesign", 0, 0),
    ]
    assert [i["code"] for i in issue_details(log)] == [
        "unknown_evidence_id",
        "unsupported_number",
        "unsupported_number",
    ]


def test_missing_model_env_var_gives_a_setup_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from processpilot.agent.catalogue import load_agent_settings
    from processpilot.agent.graph import model_id_from_env

    settings = load_agent_settings()
    monkeypatch.delenv(settings.model.env_var, raising=False)
    with pytest.raises(OSError, match=r"\.env\.example"):
        model_id_from_env(settings)
    monkeypatch.setenv(settings.model.env_var, " anthropic:claude-opus-5-5 ")
    assert model_id_from_env(settings) == "anthropic:claude-opus-5-5"


# --- Phase 4: edits applied, validated and estimated ----------------------------


def test_inapplicable_edit_is_sent_back_with_the_reason(run_dir: Path) -> None:
    bad = valid_redesign()
    # Create Order and Receive Goods are not adjacent: Approve Order is between them.
    bad["proposals"][1]["edit_operations"] = [
        {
            "op": "merge_activities",
            "activities": ["Create Order", "Receive Goods"],
            "merged_name": "X",
        }
    ]
    runner, llm = _runner(run_dir, [valid_diagnosis(), bad, valid_redesign()])
    with runner:
        _, state = runner.start(SEGMENT)
    assert state.status == "awaiting_review"
    assert [r.status for r in state.edit_results] == ["applied"] * 2
    retry_turns, _ = llm.calls[2]
    retry = retry_turns[-1].content
    assert "[edit_not_applicable]" in retry and "between them: Approve Order" in retry


def test_to_be_models_are_written_next_to_the_run_log(run_dir: Path) -> None:
    runner, _ = _runner(run_dir, [valid_diagnosis(), valid_redesign()])
    with runner:
        agent_run_id, state = runner.start(SEGMENT)
        payload = runner.pending_review(SEGMENT, agent_run_id)
    log = _log(run_dir, agent_run_id)
    assert (log.directory / "to_be_r0_p1.bpmn").is_file()
    assert (log.directory / "to_be_r0_p2.bpmn").is_file()
    # The analyst sees the outcome before deciding.
    assert payload["validation_results"][1]["estimate"]["label"] == "ESTIMATE"
    assert payload["edit_results"][0]["diff"]["added"] == ["Create and Approve Order"]
    nodes = [e.get("node") for e in log.events() if e["kind"] == "node"]
    assert "apply_edits" in nodes and "validate" in nodes


def test_run_mined_before_phase_4_fails_edits_gracefully(tmp_path: Path) -> None:
    old_run = write_run_dir(tmp_path, with_model=False)
    runner, _ = _runner(old_run, [valid_diagnosis(), valid_redesign()])
    with runner:
        _, state = runner.start(SEGMENT)
    assert state.status == "awaiting_review"
    assert [r.status for r in state.edit_results] == ["failed"] * 2
    assert "re-run mining" in state.edit_results[0].message
    assert [r.status for r in state.validation_results] == ["skipped"] * 2


def test_prompt_lists_unmodelled_activities(tmp_path: Path) -> None:
    from agent_fixtures import ACTIVITIES, as_is_tree
    from processpilot.mining.run import AS_IS_TREE_FILE
    from processpilot.process_tree import leaf, node
    from processpilot.redesign.bpmn_edit import save_tree

    run = write_run_dir(tmp_path)
    # The model leaves Approve Order out (as the inductive miner does with rare activities).
    without = node("sequence", *(leaf(a) for a in ACTIVITIES if a != "Approve Order"))
    assert without != as_is_tree()
    save_tree(without, run / SLUG / AS_IS_TREE_FILE)
    runner, llm = _runner(run, [valid_diagnosis(), valid_redesign(), valid_redesign()])
    with runner:
        runner.start(SEGMENT)
    turns, _ = llm.calls[0]
    prompt = turns[-1].content
    assert "too rare to be part of the as-is model" in prompt
    assert "- Approve Order" in prompt.split("too rare")[1].split("##")[0]
