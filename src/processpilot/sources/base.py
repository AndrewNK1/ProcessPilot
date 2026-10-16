"""The ``DataSource`` abstract base class.

WHY an ABC rather than a function per format: the Streamlit app and the agent
pipeline must be able to accept a source they cannot inspect. ``describe()``
exists so a run log can record provenance (what was read, how big it was)
without the caller needing to know the concrete type.

Contract:
    ``fetch() -> pd.DataFrame``  raw rows, original column names, no coercion
    ``describe() -> dict``       source type, human-readable name, row count

Implemented in Phase 1.
"""
