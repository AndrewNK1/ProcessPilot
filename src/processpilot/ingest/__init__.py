"""Ingest: turn raw source rows into a validated canonical event log.

Two steps, deliberately separate: ``mapping`` decides *which column means what*
(a judgement an analyst must be able to override) and ``validate`` reports what
is wrong with the data (a report, never an automatic repair).
"""
