"""SQL source via SQLAlchemy (read-only).

WHY it exists before it is needed: it proves the ``DataSource`` abstraction is
real rather than an XES wrapper with extra steps. SMEs keep process data in an
ERP database far more often than in a XES file.

Stub in Phase 1, functional in Phase 5.
"""
