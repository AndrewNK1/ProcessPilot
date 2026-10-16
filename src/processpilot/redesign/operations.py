"""Typed edit operations: the only vocabulary the redesign agent may speak.

WHY a closed set of operations rather than free-text recommendations: a
proposal expressed as typed operations can be applied, validated for
soundness, and estimated. Free text can only be read.

Operations (Phase 4): ``remove_activity``, ``parallelize_activities``,
``reorder_activities``, ``add_activity``, ``merge_activities``. Each validates
its own arguments (activities must exist in the as-is model, and must not be in
``protected_activities``).
"""
