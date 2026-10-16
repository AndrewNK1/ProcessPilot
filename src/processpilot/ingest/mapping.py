"""Column mapping: raw columns -> canonical schema.

WHY suggestions are ranked and never auto-applied: a wrong case-id column
produces a plausible-looking but entirely fictional process model. The target
user is a business analyst, not a data scientist, so the system must show its
reasoning (name pattern + data profile + confidence) and let a human confirm.

Pieces (Phase 1):
    ``ColumnMapping``      typed mapping, the only thing downstream code accepts
    ``suggest_mapping``    ranked candidates per role, with confidence scores
    ``load_preset``        read a mapping from ``configs/<dataset>.yaml``
    ``apply_mapping``      produce the canonical dataframe
    ``split_by_segment``   per-segment split when ``segment_by`` is set

XES input is a special case: the standard attributes (``concept:name``,
``time:timestamp``, ``org:resource``, ``case:concept:name``) are unambiguous and
can be mapped with confidence 1.0.
"""
