"""Data sources. One interface, many origins.

Every source returns *raw* rows; mapping to the canonical schema happens in
``processpilot.ingest``. Keeping fetch and mapping separate is what lets the
mapping UI (Phase 5) show an analyst the raw columns before committing.
"""
