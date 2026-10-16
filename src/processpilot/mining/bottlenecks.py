"""Waiting-time analysis per directly-follows edge.

WHY an impact score of ``median wait x share of cases affected``: a long wait on
an edge that two cases traverse is a curiosity, not a bottleneck. Ranking by
median rather than mean keeps a handful of junk-timestamp outliers from
inventing a bottleneck that does not exist.

Per edge: frequency, median / mean / P90 waiting time, share of cases affected,
impact score. Returned ranked, so evidence IDs are stable across reruns.

Implemented in Phase 2.
"""
