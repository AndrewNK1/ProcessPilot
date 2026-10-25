"""Conformance against a reference (intended) BPMN model.

WHY a reference model must come from outside the log: a model discovered
from the same log only says how well the miner summarised it. Deviations are
meaningful only against a model of how the process is *supposed* to run, so
this check is optional and, when no model is configured, reported as not
performed (``CM-00``) instead of being silently skipped.

Method: pm4py token-based replay (``conformance_diagnostics_token_based_replay``,
verified against pm4py 2.7.23.11) on the model converted to a Petri net. It is
fast enough for BPI-sized logs, unlike alignments. Each distinct variant is
replayed once, in canonical event order, and weighted by its case count, so
pm4py's own re-sorting of tied timestamps never applies.

Two pm4py behaviours are corrected for here, both found by probing:
- replay silently skips activities that are not in the model, so a case
  containing one would count as fitting. Such cases are counted as
  non-conforming in pandas, and the activities are reported by name.
- a case that stops before the model's end does not fit, although nothing in
  it was wrong; in a log cut at extraction time that is usually a still
  running case. Conformance is therefore also reported for completed cases.

``load_reference_model`` is the only function here that touches disk.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pandas as pd
from pydantic import BaseModel

from processpilot.config import find_project_root
from processpilot.mining.prepare import VARIANT_SEP, case_end_activities, case_variant_keys
from processpilot.mining.settings import MiningSettings

if TYPE_CHECKING:
    from pm4py.objects.bpmn.obj import BPMN

REPLAY_METHOD = "token_based_replay"

DeviationKind = Literal["executed_when_not_allowed", "not_in_model"]


class ModelDeviation(BaseModel):
    """One activity that deviates from the reference model, ranked by cases affected."""

    rank: int
    activity: str
    kind: DeviationKind
    case_count: int
    case_share: float
    example_case_ids: list[str]


class ReferenceConformance(BaseModel):
    """Replay of one segment against its reference model (or why it was not run)."""

    available: bool
    skipped_reason: str | None = None
    model_name: str | None = None
    method: str | None = None
    evaluated_cases: int = 0
    conforming_cases: int = 0
    conforming_share: float = 0.0
    completed_evaluated_cases: int | None = None
    completed_conforming_cases: int | None = None
    completed_conforming_share: float | None = None
    replay_fitness: float | None = None
    cases_with_unmodelled_activities: int = 0
    cases_unfit_without_flagged_activity: int = 0
    unmodelled_activities: list[str] = []
    unobserved_model_activities: list[str] = []
    deviations: list[ModelDeviation] = []
    example_case_ids: list[str] = []


def load_reference_model(
    settings: MiningSettings, segment: str, root: Path | None = None
) -> BPMN | None:
    """Read the segment's configured reference BPMN, or ``None`` if none is set."""
    import pm4py

    rel = settings.reference_models.path_for(segment)
    if rel is None:
        return None
    path = (root or find_project_root()) / Path(rel)
    if not path.is_file():
        raise FileNotFoundError(
            f"reference model for segment {segment!r} (reference_models) not found: {path}"
        )
    return pm4py.read_bpmn(str(path))


def not_performed(reason: str) -> ReferenceConformance:
    """The result to record when no reference model applies."""
    return ReferenceConformance(available=False, skipped_reason=reason)


def check_reference_model(
    df: pd.DataFrame,
    bpmn: BPMN,
    model_name: str,
    completed_end_activities: list[str],
    top_n: int,
    max_example_cases: int,
) -> ReferenceConformance:
    """Replay every case of ``df`` on ``bpmn`` and summarise the deviations."""
    import pm4py
    from pm4py.objects.log.obj import Event, EventLog, Trace

    net, im, fm = pm4py.convert_to_petri_net(bpmn)
    model_activities = {t.label for t in net.transitions if t.label is not None}

    keys = case_variant_keys(df)
    cases_by_variant = keys.groupby(keys, sort=True).groups
    variant_keys = sorted(cases_by_variant)
    log = EventLog(
        [
            Trace([Event({"concept:name": a}) for a in key.split(VARIANT_SEP)])
            for key in variant_keys
        ]
    )
    diagnostics = pm4py.conformance_diagnostics_token_based_replay(
        log, net, im, fm, opt_parameters={"show_progress_bar": False}
    )

    observed = set(df["activity"].unique())
    unmodelled = observed - model_activities
    totals = defaultdict(int)
    affected: dict[tuple[str, DeviationKind], list[str]] = defaultdict(list)
    conforming: list[str] = []
    unmodelled_cases = 0
    unfit_unflagged = 0
    for key, diag in zip(variant_keys, diagnostics, strict=True):
        members = [str(c) for c in cases_by_variant[key]]
        n = len(members)
        for field in ("missing_tokens", "consumed_tokens", "remaining_tokens", "produced_tokens"):
            totals[field] += diag[field] * n
        activities = set(key.split(VARIANT_SEP))
        flagged = {t.label for t in diag["transitions_with_problems"] if t.label is not None}
        outside = activities & unmodelled
        for a in flagged:
            affected[(a, "executed_when_not_allowed")].extend(members)
        for a in outside:
            affected[(a, "not_in_model")].extend(members)
        if outside:
            unmodelled_cases += n
        if diag["trace_is_fit"] and not outside:
            conforming.extend(members)
        elif not diag["trace_is_fit"] and not flagged:
            unfit_unflagged += n

    evaluated = len(keys)
    ends = case_end_activities(df)
    completed = (
        set(ends.index[ends.isin(completed_end_activities)].astype(str))
        if completed_end_activities
        else None
    )
    ranked = sorted(affected.items(), key=lambda kv: (-len(kv[1]), kv[0][0], kv[0][1]))
    deviations = [
        ModelDeviation(
            rank=i,
            activity=activity,
            kind=kind,
            case_count=len(members),
            case_share=len(members) / evaluated,
            example_case_ids=sorted(members)[:max_example_cases],
        )
        for i, ((activity, kind), members) in enumerate(ranked[:top_n], start=1)
    ]
    non_conforming = sorted(set(keys.index.astype(str)) - set(conforming))
    c_eval = c_conf = None
    c_share = None
    if completed is not None:
        c_eval = len(completed)
        c_conf = len(completed & set(conforming))
        c_share = c_conf / c_eval if c_eval else 0.0
    return ReferenceConformance(
        available=True,
        model_name=model_name,
        method=REPLAY_METHOD,
        evaluated_cases=evaluated,
        conforming_cases=len(conforming),
        conforming_share=len(conforming) / evaluated if evaluated else 0.0,
        completed_evaluated_cases=c_eval,
        completed_conforming_cases=c_conf,
        completed_conforming_share=c_share,
        replay_fitness=_fitness(totals),
        cases_with_unmodelled_activities=unmodelled_cases,
        cases_unfit_without_flagged_activity=unfit_unflagged,
        unmodelled_activities=sorted(unmodelled),
        unobserved_model_activities=sorted(model_activities - observed),
        deviations=deviations,
        example_case_ids=non_conforming[:max_example_cases],
    )


def _fitness(totals: dict[str, int]) -> float:
    """pm4py's log-level token-replay fitness, recomputed from case-weighted counts."""
    consumed, produced = totals["consumed_tokens"], totals["produced_tokens"]
    missing_part = 1 - totals["missing_tokens"] / consumed if consumed else 1.0
    remaining_part = 1 - totals["remaining_tokens"] / produced if produced else 1.0
    return 0.5 * missing_part + 0.5 * remaining_part
