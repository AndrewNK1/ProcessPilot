"""File-backed source: XES, CSV, XLSX, Parquet, detected by extension.

WHY a parquet cache: parsing the 695 MB BPI 2019 XES takes minutes, and the
ingest notebook is re-run constantly during development. The cache is keyed on
the source file's size and modification time so a changed input can never be
served from a stale cache -- silent staleness would invalidate every downstream
number.

Implemented in Phase 1.
"""
