"""Read-only tools over the evidence store.

WHY read-only is enforced here rather than by instruction: the design principle
"the LLM never computes a metric" is only credible if the model has no tool
capable of computing one. These tools select and filter precomputed evidence;
none of them touches the event log or does arithmetic. They return deep copies,
so a caller cannot mutate the store through a result.

WHY the diagnose step receives a digest built from these tools instead of
calling them itself: one segment's evidence is ~60 items (~9k tokens), small
enough to show in full. A fixed digest makes the prompt identical on every
rerun (reproducible and cacheable), costs one model call instead of a tool
loop, and removes a failure mode (the model not calling a tool, or calling it
with bad arguments). The tools stay the single access path: the digest and,
in Phase 5, the UI are built on them.
"""

from __future__ import annotations

from processpilot.mining.evidence import EvidenceItem, EvidenceStore, EvidenceType


class EvidenceTools:
    """Segment-scoped, read-only queries. Items keep store (= rank) order."""

    def __init__(self, store: EvidenceStore, segment: str) -> None:
        """Bind to one segment; ``KeyError`` if the store has no such segment."""
        if segment not in store.segments():
            raise KeyError(f"segment {segment!r} not in evidence store; have {store.segments()}")
        self._store = store
        self.segment = segment

    def _of_type(self, type_: EvidenceType) -> list[EvidenceItem]:
        return [i.model_copy(deep=True) for i in self._store.query(self.segment, type_)]

    def all_items(self) -> list[EvidenceItem]:
        """Every item of the segment."""
        return [i.model_copy(deep=True) for i in self._store.query(self.segment)]

    def get_kpis(self) -> list[EvidenceItem]:
        """Segment KPI item(s)."""
        return self._of_type("kpi")

    def get_top_bottlenecks(self, n: int | None = None) -> list[EvidenceItem]:
        """The ``n`` highest-impact bottlenecks (all listed ones if ``None``)."""
        return self._of_type("bottleneck")[:n]

    def get_variants(self, n: int | None = None) -> list[EvidenceItem]:
        """The ``n`` most frequent variants (all listed ones if ``None``)."""
        return self._of_type("variant")[:n]

    def get_rework(self) -> list[EvidenceItem]:
        """Rework items, ranked by impact."""
        return self._of_type("rework")

    def get_resources(self) -> list[EvidenceItem]:
        """Workload (RS-*) and handover (HO-*) items, or the RS-00 skip notice."""
        return self._of_type("resource")

    def get_conformance(self) -> list[EvidenceItem]:
        """Conformance-rule results for rules that apply to this segment."""
        return self._of_type("conformance")

    def get_evidence(self, evidence_id: str) -> EvidenceItem:
        """One item by id; ``KeyError`` if unknown or from another segment."""
        item = self._store.get(evidence_id)
        if item.segment != self.segment:
            raise KeyError(f"evidence {evidence_id} belongs to segment {item.segment!r}")
        return item.model_copy(deep=True)

    def segment_store(self) -> EvidenceStore:
        """A store with only this segment's items -- the citation universe."""
        return EvidenceStore(items=self.all_items())
