"""The RQ1 answer key: the reference list a diagnosis is scored against.

WHY it is computed by code from the frozen mining run, before any LLM run:
the reference must not be influenced by what the model says. The rule is
fixed in the thesis methodology (Chapter 3) and its parameters live in
``configs/eval.yaml`` (``answer_key``):

- per segment, the top-N bottlenecks by impact (BN-01..BN-N, the mining
  ranking), with source -> target, median wait and case share;
- plus every conformance rule (CF item) whose completed-case violation share
  is strictly greater than the threshold.

If no rule passes, the key holds the bottlenecks only and says so explicitly
(user decision 2026-11-18). Every rule's completed-case rate is stored either
way (``conformance_rates``), so the thesis can report them.

Matching (applied in Phase 7, stored here so it cannot drift):

- strict: a hypothesis cites the key item's evidence ID;
- lenient: strict, or it cites one of the item's ``related_evidence_ids``
  (fixed rule below), or (Phase 7) its text names the item's ``activities``.

Related evidence IDs: for a bottleneck a -> b, the AW item of b and the HO/ST
items on the same edge a -> b; for a conformance rule, the AW items of the
activities the rule names.

The key is written once (``write_answer_key`` refuses to overwrite and marks
the files read-only); its SHA-256 goes into the campaign manifest.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from processpilot.config import load_config_yaml
from processpilot.mining.evidence import EvidenceItem, EvidenceStore, format_duration

ANSWER_KEY_VERSION = 1
ANSWER_KEY_JSON = "answer_key.json"
ANSWER_KEY_CSV = "answer_key.csv"


class AnswerKeySettings(BaseModel):
    """``answer_key`` block of ``configs/eval.yaml``."""

    top_n_bottlenecks: int = Field(gt=0)
    conformance_completed_share_gt: float = Field(ge=0, lt=1)

    @classmethod
    def load(cls) -> AnswerKeySettings:
        """Read the block from ``configs/eval.yaml``."""
        return cls.model_validate(load_config_yaml("eval.yaml")["answer_key"])


class KeyItem(BaseModel):
    """One item a diagnosis should find."""

    evidence_id: str
    kind: Literal["bottleneck", "conformance"]
    rank: int | None = None
    source: str | None = None
    target: str | None = None
    median_wait_seconds: float | None = None
    median_wait: str | None = None
    case_share: float | None = None
    rule_id: str | None = None
    rule_name: str | None = None
    completed_violation_share: float | None = None
    summary: str
    activities: list[str]
    related_evidence_ids: list[str]


class ConformanceRate(BaseModel):
    """Every rule of a segment, whether or not it entered the key."""

    evidence_id: str
    rule_id: str
    rule_name: str
    rule_type: str
    completed_violation_count: int | None
    completed_evaluated_cases: int | None
    completed_violation_share: float | None
    completed_excluded_cases: int | None
    completed_excluded_ending_in: list[str]
    violation_count: int
    evaluated_cases: int
    violation_share: float
    activities_missing_from_segment: list[str]
    in_key: bool
    reason: str


class SegmentKey(BaseModel):
    """The key of one segment."""

    segment: str
    bottlenecks: list[KeyItem]
    conformance_items: list[KeyItem]
    key_contents: str
    conformance_rates: list[ConformanceRate]
    notes: list[str] = Field(default_factory=list)


class AnswerKey(BaseModel):
    """The whole reference list, with what it was built from."""

    answer_key_version: int = ANSWER_KEY_VERSION
    campaign_id: str
    mining_run_id: str
    evidence_schema_version: int
    evidence_sha256: str
    created_at: str
    rule: dict[str, Any]
    matching: dict[str, str]
    segments: list[SegmentKey]


def _rule_text(settings: AnswerKeySettings) -> dict[str, Any]:
    return {
        "top_n_bottlenecks": settings.top_n_bottlenecks,
        "conformance_completed_share_gt": settings.conformance_completed_share_gt,
        "text": (
            f"Per segment: the top {settings.top_n_bottlenecks} bottlenecks by impact "
            "(median wait x case share), plus every conformance rule violated in more than "
            f"{settings.conformance_completed_share_gt:.0%} of completed cases (strictly "
            "greater). If no rule passes, the key holds the bottlenecks only."
        ),
    }


MATCHING = {
    "strict": "the diagnosis cites the key item's evidence_id",
    "lenient": (
        "strict, or the diagnosis cites one of the item's related_evidence_ids, or (Phase 7) "
        "its text names the item's activities"
    ),
    "related_evidence_ids": (
        "bottleneck a -> b: the AW item of b and the HO/ST items on edge a -> b; "
        "conformance rule: the AW items of the activities the rule names"
    ),
}


def _related(
    items: list[EvidenceItem], wait_activities: list[str], edge: tuple[str, str] | None
) -> list[str]:
    """AW items of ``wait_activities`` and HO/ST items on ``edge``, in store order."""
    out = []
    for it in items:
        prefix = it.id.split("-", 1)[0]
        m = it.metrics
        if prefix == "AW" and m.get("activity") in wait_activities:
            out.append(it.id)
        elif prefix in ("HO", "ST") and edge is not None:
            if (m.get("source"), m.get("target")) == edge:
                out.append(it.id)
    return out


def _bottleneck(item: EvidenceItem, items: list[EvidenceItem]) -> KeyItem:
    m = item.metrics
    source, target = str(m["source"]), str(m["target"])
    return KeyItem(
        evidence_id=item.id,
        kind="bottleneck",
        rank=int(m["rank"]),
        source=source,
        target=target,
        median_wait_seconds=float(m["median_wait_seconds"]),
        median_wait=format_duration(float(m["median_wait_seconds"])),
        case_share=float(m["case_share"]),
        summary=item.summary,
        activities=[source, target],
        related_evidence_ids=_related(items, [target], (source, target)),
    )


def _rule_activities(m: dict[str, Any]) -> list[str]:
    params = m.get("params") or {}
    keys = ("a", "b") if m.get("rule_type") == "activity_a_before_activity_b" else ("activity",)
    return [str(params[k]) for k in keys if k in params]


def _rate(item: EvidenceItem, threshold: float) -> ConformanceRate:
    m = item.metrics
    share = m.get("completed_violation_share")
    if share is None:
        in_key, reason = False, "no completed-case figure (no case-completion definition)"
    elif share > threshold:
        in_key, reason = True, f"completed-case share above {threshold:.0%}"
    else:
        in_key, reason = False, f"completed-case share at or below {threshold:.0%}"
    if m.get("activities_missing_from_segment"):
        reason += "; the rule names activities never observed in this segment"
    return ConformanceRate(
        evidence_id=item.id,
        rule_id=str(m["rule_id"]),
        rule_name=str(m["rule_name"]),
        rule_type=str(m["rule_type"]),
        completed_violation_count=m.get("completed_violation_count"),
        completed_evaluated_cases=m.get("completed_evaluated_cases"),
        completed_violation_share=share,
        completed_excluded_cases=m.get("completed_excluded_cases"),
        completed_excluded_ending_in=list(m.get("completed_excluded_ending_in") or []),
        violation_count=int(m["violation_count"]),
        evaluated_cases=int(m["evaluated_cases"]),
        violation_share=float(m["violation_share"]),
        activities_missing_from_segment=list(m.get("activities_missing_from_segment") or []),
        in_key=in_key,
        reason=reason,
    )


def segment_key(store: EvidenceStore, segment: str, settings: AnswerKeySettings) -> SegmentKey:
    """Apply the rule to one segment's evidence (pure)."""
    items = store.query(segment)
    notes: list[str] = []
    bns = sorted(
        (i for i in items if i.type == "bottleneck"), key=lambda i: int(i.metrics["rank"])
    )[: settings.top_n_bottlenecks]
    if len(bns) < settings.top_n_bottlenecks:
        notes.append(
            f"only {len(bns)} bottleneck(s) exist; the key holds fewer than "
            f"{settings.top_n_bottlenecks}"
        )
    rules = [i for i in items if i.type == "conformance" and "rule_id" in i.metrics]
    rates = [_rate(r, settings.conformance_completed_share_gt) for r in rules]
    conformance = []
    for rule_item, rate in zip(rules, rates, strict=True):
        if not rate.in_key:
            continue
        acts = _rule_activities(rule_item.metrics)
        conformance.append(
            KeyItem(
                evidence_id=rule_item.id,
                kind="conformance",
                rule_id=rate.rule_id,
                rule_name=rate.rule_name,
                completed_violation_share=rate.completed_violation_share,
                summary=rule_item.summary,
                activities=acts,
                related_evidence_ids=_related(items, acts, None),
            )
        )
    if conformance:
        contents = "bottlenecks and conformance rules"
    elif rates:
        contents = (
            "bottlenecks only (no conformance rule above "
            f"{settings.conformance_completed_share_gt:.0%} of completed cases)"
        )
    else:
        contents = "bottlenecks only (no conformance rule applies to this segment)"
    return SegmentKey(
        segment=segment,
        bottlenecks=[_bottleneck(b, items) for b in bns],
        conformance_items=conformance,
        key_contents=contents,
        conformance_rates=rates,
        notes=notes,
    )


def build_answer_key(
    store: EvidenceStore,
    segments: list[str],
    settings: AnswerKeySettings,
    *,
    campaign_id: str,
    mining_run_id: str,
    evidence_sha256: str,
    created_at: str,
) -> AnswerKey:
    """The key for every segment, in the given (manifest) order."""
    return AnswerKey(
        campaign_id=campaign_id,
        mining_run_id=mining_run_id,
        evidence_schema_version=store.schema_version,
        evidence_sha256=evidence_sha256,
        created_at=created_at,
        rule=_rule_text(settings),
        matching=MATCHING,
        segments=[segment_key(store, s, settings) for s in segments],
    )


_CSV_COLUMNS = (
    "segment",
    "evidence_id",
    "kind",
    "rank",
    "source",
    "target",
    "median_wait",
    "median_wait_seconds",
    "case_share",
    "rule_id",
    "completed_violation_share",
    "activities",
    "related_evidence_ids",
)


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, read in chunks (works for large parquet/XES files)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_answer_key(key: AnswerKey, directory: Path) -> tuple[Path, Path, str]:
    """Write JSON + CSV once, read-only. Returns (json path, csv path, JSON SHA-256)."""
    json_path, csv_path = directory / ANSWER_KEY_JSON, directory / ANSWER_KEY_CSV
    for path in (json_path, csv_path):
        if path.exists():
            raise FileExistsError(
                f"{path} exists; the answer key is written once and never replaced"
            )
    directory.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(key.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for seg in key.segments:
            for item in [*seg.bottlenecks, *seg.conformance_items]:
                row = item.model_dump()
                writer.writerow(
                    {
                        **{c: row.get(c) for c in _CSV_COLUMNS if c in row},
                        "segment": seg.segment,
                        "activities": " | ".join(item.activities),
                        "related_evidence_ids": " ".join(item.related_evidence_ids),
                    }
                )
    for path in (json_path, csv_path):
        os.chmod(path, stat.S_IREAD)
    return json_path, csv_path, sha256_file(json_path)
