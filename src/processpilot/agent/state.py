"""Typed LangGraph state.

WHY pydantic state rather than a loose dict: the state carries the analyst's
constraints and the evidence IDs that proposals must cite. Both are things the
graph has to enforce, and enforcement needs a schema.

Holds (Phase 3): run id, segment, evidence items, diagnosis, proposals, applied
BPMN paths, validation result, analyst constraints (free text plus the
machine-readable ``protected_activities`` subset), review decision, and the
redesign loop counter (max 2 rejections).
"""
