"""The ``DataSource`` abstract base class.

WHY an ABC rather than a function per format: the Streamlit app and the agent
pipeline must be able to accept a source they cannot inspect. ``describe()``
exists so a run log can record provenance (what was read, how big it was)
without the caller needing to know the concrete type.

Contract:
    ``fetch() -> pd.DataFrame``  raw rows, original column names, no coercion
    ``describe() -> dict``       source type, human-readable name, row count
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class DataSource(ABC):
    """Something that can produce raw event rows plus provenance metadata."""

    @abstractmethod
    def fetch(self) -> pd.DataFrame:
        """Return raw rows with their original column names, uncoerced."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Return provenance: at least ``type``, ``name``, and ``row_count``."""
