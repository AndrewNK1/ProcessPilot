"""Trace variants and the happy path.

WHY variants matter to redesign: the gap between the most frequent variant's
throughput time and the log's median is the size of the prize. Variants also
tell the redesign agent which exceptional paths it must not break.

Top-N variants with case share and mean throughput time; the most frequent
variant is labelled the happy path (frequency, not desirability -- the
distinction is documented in the evidence summary).

Implemented in Phase 2.
"""
