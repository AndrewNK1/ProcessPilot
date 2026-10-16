"""Rule-based conformance checking.

WHY declarative rules instead of only alignment-based conformance: an analyst
cares that "the invoice was recorded before the goods receipt in 12% of cases",
which is a domain rule, not a model deviation. Rules are data in
``configs/*.yaml``; only the rule *types* are code, which keeps the engine
dataset-agnostic.

Rule types (Phase 2): ``activity_a_before_activity_b``, ``activity_must_occur``.
Output per rule: violation count, share of cases, example case ids.
"""
