"""Read-only tools over the evidence store.

WHY read-only is enforced here rather than by instruction: the design principle
"the LLM never computes a metric" is only credible if the model has no tool
capable of computing one. These tools select and filter precomputed evidence;
none of them touches the event log or does arithmetic.

Tools (Phase 3): ``get_top_bottlenecks``, ``get_variants``, ``get_rework``,
``get_conformance``, ``get_kpis``, ``get_evidence(id)``.
"""
