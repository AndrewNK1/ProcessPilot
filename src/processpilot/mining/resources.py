"""Resource workload, handovers, and system/vendor transitions.

WHY handovers get their own metric: waiting time concentrated at a handover
points at an organisational fix (numerical involvement, extra resources) while
the same waiting inside one resource's queue points at a capacity fix. The
heuristic chosen depends on which it is.

WHY resource types (evidence schema 2): only people hand work over to each
other. Resources are first classified (``ingest.resource_types``) as human,
batch (system jobs) or unassigned (no user recorded). Then:

* workload ranks **human** resources only; batch and unassigned are
  summarised per type (share of events, top activities), so nothing is lost;
* a handover is a step between two **different human** resources, and the
  baseline is the same person continuing;
* a step with a batch job or an unassigned event on either side is a
  **system/vendor transition**, reported separately and never called a handover.

Skipped entirely, with an explicit note in the evidence store, when the log has
no resource column -- a silent skip would let the agent assume no resource
problem exists.

Workload is counted on the *raw* canonical log (every posting is work someone
did); handovers and transitions on the mining view (collapsed batch postings),
so they line up with the DFG edges.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from processpilot.ingest.resource_types import (
    RESOURCE_TYPES,
    ResourceType,
    ResourceTypeSettings,
    classify_resources,
)
from processpilot.mining.prepare import directly_follows_pairs

# Words used in code-written evidence text, per type.
TYPE_LABELS: dict[str, str] = {
    "human": "person",
    "batch": "batch job",
    "unassigned": "no user recorded",
}


class ResourceWorkload(BaseModel):
    """Events and cases handled by one human resource."""

    rank: int
    resource: str
    resource_type: ResourceType = "human"
    event_count: int
    # Share of all events of the segment, and of its human events only.
    event_share: float
    human_event_share: float
    case_count: int
    distinct_activities: int


class ActivityCount(BaseModel):
    """One activity's events within a resource type."""

    activity: str
    event_count: int
    share_of_type_events: float


class ResourceTypeSummary(BaseModel):
    """One resource type's share of the segment (raw log)."""

    type: ResourceType
    event_count: int
    event_share: float
    case_count: int
    case_share: float
    distinct_resources: int
    top_activities: list[ActivityCount]


class HandoverEdge(BaseModel):
    """Waits on one activity edge, split by whether the person changed.

    Shares are of *all* transitions on the edge, so ``handover_share`` +
    ``same_person_share`` + ``system_transition_share`` = 1.
    """

    rank: int
    source: str
    target: str
    handover_count: int
    handover_case_count: int
    handover_case_share: float
    handover_share_of_transitions: float
    system_transition_share_of_transitions: float
    median_handover_wait_seconds: float
    median_same_resource_wait_seconds: float | None
    impact_score: float


class SystemTransitionEdge(BaseModel):
    """Transitions on one edge that involve a batch job or no recorded user."""

    rank: int
    source: str
    target: str
    transition_count: int
    case_count: int
    case_share: float
    share_of_transitions: float
    median_wait_seconds: float
    impact_score: float
    # "human -> unassigned": count, ordered by count desc then name.
    kinds: dict[str, int]
    dominant_kind: str
    dominant_kind_share: float


class ResourceAnalysis(BaseModel):
    """Workload + handovers, or an explicit statement that they were skipped."""

    available: bool
    skipped_reason: str | None = None
    resource_types_configured: bool = False
    resource_type_rules: str | None = None
    # Events classified ``unassigned`` (no user recorded, incl. empty values).
    unassigned_event_count: int = 0
    # Between two different people / the same person continuing.
    median_handover_wait_seconds: float | None = None
    median_same_resource_wait_seconds: float | None = None
    handover_transition_count: int = 0
    same_resource_transition_count: int = 0
    system_transition_count: int = 0
    median_system_transition_wait_seconds: float | None = None
    type_summaries: list[ResourceTypeSummary] = []
    workload: list[ResourceWorkload] = []
    handovers: list[HandoverEdge] = []
    system_transitions: list[SystemTransitionEdge] = []


def analyse_resources(
    raw_df: pd.DataFrame,
    mining_df: pd.DataFrame,
    top_n_resources: int,
    top_n_handovers: int,
    *,
    top_n_system_transitions: int,
    top_n_type_activities: int,
    resource_types: ResourceTypeSettings | None = None,
) -> ResourceAnalysis:
    """Typed workload (raw log) and handover / transition rankings (mining view).

    ``resource_types=None`` means no rules: every recorded value is human.
    """
    if "resource" not in raw_df.columns or raw_df["resource"].isna().all():
        return ResourceAnalysis(
            available=False,
            skipped_reason="the log has no resource column (or it is entirely empty)",
        )
    settings = resource_types or ResourceTypeSettings()
    raw_types, _ = classify_resources(raw_df["resource"], settings)
    return ResourceAnalysis(
        available=True,
        resource_types_configured=settings.configured,
        resource_type_rules=settings.describe(),
        unassigned_event_count=int((raw_types == "unassigned").sum()),
        type_summaries=_type_summaries(raw_df, raw_types, top_n_type_activities),
        workload=_workload(raw_df, raw_types, top_n_resources),
        **_transitions(mining_df, settings, top_n_handovers, top_n_system_transitions),
    )


def _type_summaries(df: pd.DataFrame, types: pd.Series, top_n: int) -> list[ResourceTypeSummary]:
    total_events, total_cases = len(df), int(df["case_id"].nunique())
    out = []
    for t in RESOURCE_TYPES:
        part = df.loc[types == t]
        if part.empty:
            continue
        acts = part.groupby("activity", sort=True).size().reset_index(name="n")
        acts = acts.sort_values(["n", "activity"], ascending=[False, True], kind="stable")
        cases = int(part["case_id"].nunique())
        out.append(
            ResourceTypeSummary(
                type=t,
                event_count=len(part),
                event_share=len(part) / total_events,
                case_count=cases,
                case_share=cases / total_cases,
                distinct_resources=int(part["resource"].nunique(dropna=False)),
                top_activities=[
                    ActivityCount(
                        activity=str(a.activity),
                        event_count=int(a.n),
                        share_of_type_events=int(a.n) / len(part),
                    )
                    for a in acts.head(top_n).itertuples(index=False)
                ],
            )
        )
    return out


def _workload(df: pd.DataFrame, types: pd.Series, top_n: int) -> list[ResourceWorkload]:
    human = df.loc[types == "human"]
    stats = (
        human.groupby("resource", sort=True)
        .agg(
            event_count=("activity", "size"),
            case_count=("case_id", "nunique"),
            distinct_activities=("activity", "nunique"),
        )
        .reset_index()
        .sort_values(["event_count", "resource"], ascending=[False, True], kind="stable")
    )
    total, n_human = len(df), len(human)
    return [
        ResourceWorkload(
            rank=rank,
            resource=str(r.resource),
            event_count=int(r.event_count),
            event_share=float(r.event_count) / total,
            human_event_share=float(r.event_count) / n_human,
            case_count=int(r.case_count),
            distinct_activities=int(r.distinct_activities),
        )
        for rank, r in enumerate(stats.head(top_n).itertuples(index=False), start=1)
    ]


def _median(s: pd.Series) -> float | None:
    return float(s.median()) if len(s) else None


def _transitions(
    df: pd.DataFrame, settings: ResourceTypeSettings, top_n_hand: int, top_n_sys: int
) -> dict[str, object]:
    pairs = directly_follows_pairs(df)
    pairs["source_type"] = classify_resources(pairs["source_resource"], settings)[0]
    pairs["target_type"] = classify_resources(pairs["target_resource"], settings)[0]
    both_human = (pairs["source_type"] == "human") & (pairs["target_type"] == "human")
    pairs["kind"] = "system"
    pairs.loc[both_human, "kind"] = "same"
    pairs.loc[both_human & (pairs["source_resource"] != pairs["target_resource"]), "kind"] = (
        "handover"
    )
    total_cases = int(df["case_id"].nunique())

    hand_edges: list[HandoverEdge] = []
    sys_edges: list[SystemTransitionEdge] = []
    for (source, target), edge in pairs.groupby(["source", "target"], sort=True):
        n = len(edge)
        hand = edge.loc[edge["kind"] == "handover"]
        system = edge.loc[edge["kind"] == "system"]
        if not hand.empty:
            hand_cases = int(hand["case_id"].nunique())
            median_hand = float(hand["wait_seconds"].median())
            hand_edges.append(
                HandoverEdge(
                    rank=0,
                    source=str(source),
                    target=str(target),
                    handover_count=len(hand),
                    handover_case_count=hand_cases,
                    handover_case_share=hand_cases / total_cases,
                    handover_share_of_transitions=len(hand) / n,
                    system_transition_share_of_transitions=len(system) / n,
                    median_handover_wait_seconds=median_hand,
                    median_same_resource_wait_seconds=_median(
                        edge.loc[edge["kind"] == "same", "wait_seconds"]
                    ),
                    impact_score=median_hand * hand_cases / total_cases,
                )
            )
        if not system.empty:
            sys_cases = int(system["case_id"].nunique())
            median_sys = float(system["wait_seconds"].median())
            labels = system["source_type"] + " -> " + system["target_type"]
            kinds = sorted(labels.value_counts().items(), key=lambda kv: (-kv[1], kv[0]))
            sys_edges.append(
                SystemTransitionEdge(
                    rank=0,
                    source=str(source),
                    target=str(target),
                    transition_count=len(system),
                    case_count=sys_cases,
                    case_share=sys_cases / total_cases,
                    share_of_transitions=len(system) / n,
                    median_wait_seconds=median_sys,
                    impact_score=median_sys * sys_cases / total_cases,
                    kinds={k: int(v) for k, v in kinds},
                    dominant_kind=str(kinds[0][0]),
                    dominant_kind_share=int(kinds[0][1]) / len(system),
                )
            )

    def ranked(edges: list, top_n: int) -> list:
        edges.sort(key=lambda e: (-e.impact_score, e.source, e.target))
        return [e.model_copy(update={"rank": r}) for r, e in enumerate(edges[:top_n], start=1)]

    kinds = ("handover", "same", "system")
    by_kind = {k: pairs.loc[pairs["kind"] == k, "wait_seconds"] for k in kinds}
    return {
        "median_handover_wait_seconds": _median(by_kind["handover"]),
        "median_same_resource_wait_seconds": _median(by_kind["same"]),
        "handover_transition_count": len(by_kind["handover"]),
        "same_resource_transition_count": len(by_kind["same"]),
        "system_transition_count": len(by_kind["system"]),
        "median_system_transition_wait_seconds": _median(by_kind["system"]),
        "handovers": ranked(hand_edges, top_n_hand),
        "system_transitions": ranked(sys_edges, top_n_sys),
    }
