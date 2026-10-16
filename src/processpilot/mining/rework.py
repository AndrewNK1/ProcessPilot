"""Rework and loop detection.

WHY it is measured separately from bottlenecks: a repeated activity is a
*quality* signal, not a waiting signal, and it maps to different redesign
heuristics (knock-out ordering, resequencing) than a long queue does.

Per activity: repetition count within a case, share of cases affected, and the
extra elapsed time attributable to repetitions.

Implemented in Phase 2.
"""
