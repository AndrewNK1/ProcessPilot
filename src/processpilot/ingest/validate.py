"""Data-quality validation. Reports problems; never fixes them silently.

WHY the strict separation between ``validate`` and ``apply_cleaning``: dropping
rows changes every downstream metric. For a thesis the removals have to be
defensible and reproducible, so the caller chooses an explicit policy and every
removal is recorded in the report that accompanies the results.

Checks (Phase 1): nulls in required columns; unparseable timestamps; timestamps
outside a configured plausible range (BPI 2019 contains 1948 and 2070 dates);
single-event cases; duplicate events; non-monotonic timestamps within a case
after sorting; timestamp ties within a case (these make directly-follows order
ambiguous, so they bound the trustworthiness of every waiting-time number);
activity cardinality; overall log size.

Each issue carries severity (info/warn/error), a count, a share of rows, and
example rows so an analyst can look at the actual data behind the warning.
"""
