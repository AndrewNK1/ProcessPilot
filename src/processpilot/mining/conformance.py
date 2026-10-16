"""Rule-based conformance checking.

WHY declarative rules instead of only alignment-based conformance: an analyst
cares that "the invoice was recorded before the goods receipt in 12% of cases",
which is a domain rule, not a model deviation. Rules are data in
``configs/*.yaml``; only the rule *types* are code, which keeps the engine
dataset-agnostic.

Rule types: ``activity_a_before_activity_b``, ``activity_must_occur``.
Output per rule: violation count, share of cases, example case ids -- each
reported twice, for all evaluated cases and for completed cases only, because
an event log is a time window and "activity never happened" in a case that was
still running is not a violation (see ``CaseCompletionSettings``).

Semantics, chosen to be conservative:
- before-rule: evaluated only on cases containing both activities; violated
  when the first ``b`` is strictly earlier than the first ``a``. Equal
  timestamps are counted separately as ``tied_cases``, never as violations,
  because their order is unknown.
- must-occur: evaluated on every case; violated when the activity is absent.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.mining.prepare import case_end_activities
from processpilot.mining.settings import ConformanceRule


class ConformanceResult(BaseModel):
    """Outcome of one rule on one segment."""

    rule_id: str
    rule_name: str
    rule_type: str
    description: str
    params: dict[str, str]
    evaluated_cases: int
    violation_count: int
    violation_share: float
    completed_evaluated_cases: int | None
    completed_violation_count: int | None
    completed_violation_share: float | None
    tied_cases: int | None
    activities_missing_from_segment: list[str]
    example_case_ids: list[str]


def check_rules(
    df: pd.DataFrame,
    segment: str,
    rules: list[ConformanceRule],
    completed_end_activities: list[str],
    max_example_cases: int,
) -> list[ConformanceResult]:
    """Evaluate every rule that applies to ``segment``, in preset order."""
    ends = case_end_activities(df)
    completed = (
        set(ends.index[ends.isin(completed_end_activities)]) if completed_end_activities else None
    )
    present = set(df["activity"].unique())
    first_seen = df.groupby(["case_id", "activity"], sort=True)["timestamp"].min()

    results: list[ConformanceResult] = []
    for rule in rules:
        if not rule.applies_to(segment):
            continue
        if rule.type == "activity_a_before_activity_b":
            evaluated, violating, tied = _before(first_seen, rule.params["a"], rule.params["b"])
            named = [rule.params["a"], rule.params["b"]]
        else:
            evaluated, violating = _must_occur(df, first_seen, rule.params["activity"])
            tied = None
            named = [rule.params["activity"]]
        results.append(
            _result(rule, evaluated, violating, tied, completed, present, named, max_example_cases)
        )
    return results


def _cases_with(first_seen: pd.Series, activity: str) -> pd.Series:
    """First timestamp of ``activity`` per case that contains it."""
    try:
        return first_seen.xs(activity, level="activity")
    except KeyError:
        return pd.Series(dtype="datetime64[ns]")


def _before(first_seen: pd.Series, a: str, b: str) -> tuple[set[str], set[str], int]:
    first_a = _cases_with(first_seen, a)
    first_b = _cases_with(first_seen, b)
    both = first_a.index.intersection(first_b.index)
    fa, fb = first_a.loc[both], first_b.loc[both]
    return set(both), set(both[fb < fa]), int((fb == fa).sum())


def _must_occur(
    df: pd.DataFrame, first_seen: pd.Series, activity: str
) -> tuple[set[str], set[str]]:
    all_cases = set(df["case_id"].unique())
    return all_cases, all_cases - set(_cases_with(first_seen, activity).index)


def _result(
    rule: ConformanceRule,
    evaluated: set[str],
    violating: set[str],
    tied: int | None,
    completed: set[str] | None,
    present: set[str],
    named: list[str],
    max_example_cases: int,
) -> ConformanceResult:
    if completed is None:
        c_eval = c_viol = None
        c_share = None
    else:
        c_eval = len(evaluated & completed)
        c_viol = len(violating & completed)
        c_share = c_viol / c_eval if c_eval else 0.0
    return ConformanceResult(
        rule_id=rule.id,
        rule_name=rule.name,
        rule_type=rule.type,
        description=rule.description,
        params=rule.params,
        evaluated_cases=len(evaluated),
        violation_count=len(violating),
        violation_share=len(violating) / len(evaluated) if evaluated else 0.0,
        completed_evaluated_cases=c_eval,
        completed_violation_count=c_viol,
        completed_violation_share=c_share,
        tied_cases=tied,
        activities_missing_from_segment=[a for a in named if a not in present],
        example_case_ids=[str(c) for c in sorted(violating)[:max_example_cases]],
    )
