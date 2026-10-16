"""Canonical event-log schema and the pydantic models shared across modules.

WHY a canonical schema: every data source (XES file, CSV, SQL, Odoo) produces a
different shape. Mining code must not know where data came from, so all sources
converge here first. That single conversion point is also the only place where
timestamp and dtype guarantees are established, which is what makes the mining
functions pure.

Guarantees after ``apply_mapping`` + validation (Phase 1):
    - columns ``case_id`` (str), ``activity`` (str), ``timestamp``
      (tz-naive ``datetime64[ns]``) are present and non-null
    - optional ``resource`` (str) and ``segment`` (str)
    - all other source columns preserved under an ``attr:`` prefix so no
      information is silently dropped
    - stably sorted by ``(case_id, timestamp)`` so event order within equal
      timestamps reflects the source order rather than an arbitrary permutation

Implemented in Phase 1.
"""
