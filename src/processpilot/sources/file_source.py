"""File-backed source: XES, CSV, XLSX, Parquet, detected by extension.

WHY a parquet cache: parsing the 695 MB BPI 2019 XES takes minutes, and the
ingest notebook is re-run constantly during development. The cache is keyed on
the source file's size and modification time so a changed input can never be
served from a stale cache -- silent staleness would invalidate every downstream
number. Only XES parsing is cached: CSV/XLSX/Parquet reads are already cheap,
so caching them would just duplicate storage for no benefit.

``fetch()`` returns rows exactly as the underlying parser produced them -- no
timezone conversion, no renaming. That coercion belongs to
``ingest.mapping.apply_mapping``, which is the single place the canonical
schema's guarantees are established.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from processpilot.sources.base import DataSource

_SUPPORTED_SUFFIXES = {".xes", ".csv", ".xlsx", ".xls", ".parquet"}

# Repo root, assuming this file stays at src/processpilot/sources/file_source.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CACHE_DIR = _REPO_ROOT / "data" / "processed"


class FileSource(DataSource):
    """Reads one local file (XES/CSV/XLSX/Parquet) into a raw dataframe."""

    def __init__(self, path: Path | str, cache_dir: Path | str | None = None) -> None:
        """Set up the source; raises if ``path`` doesn't exist or isn't a supported type."""
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"source file not found: {self.path}")
        suffix = self.path.suffix.lower()
        if suffix not in _SUPPORTED_SUFFIXES:
            raise ValueError(
                f"unsupported file type '{suffix}' for {self.path}; "
                f"supported: {sorted(_SUPPORTED_SUFFIXES)}"
            )
        self._suffix = suffix
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_CACHE_DIR
        self._row_count: int | None = None

    def fetch(self) -> pd.DataFrame:
        """Return raw rows with their original column names, uncoerced."""
        if self._suffix == ".xes":
            df = self._fetch_xes_cached()
        elif self._suffix == ".csv":
            df = self._fetch_csv()
        elif self._suffix in (".xlsx", ".xls"):
            df = pd.read_excel(self.path)
        elif self._suffix == ".parquet":
            df = pd.read_parquet(self.path)
        else:  # pragma: no cover -- guarded by __init__
            raise AssertionError(f"unreachable suffix {self._suffix}")
        self._row_count = len(df)
        return df

    def describe(self) -> dict[str, Any]:
        """Return provenance: type, name, and row count (fetches if needed)."""
        if self._row_count is None:
            self.fetch()
        return {
            "type": "file",
            "name": self.path.name,
            "path": str(self.path),
            "format": self._suffix.lstrip("."),
            "row_count": self._row_count,
            "size_bytes": self.path.stat().st_size,
        }

    # --- XES, with a size+mtime-keyed parquet cache --------------------

    def _cache_paths(self) -> tuple[Path, Path]:
        cache_path = self.cache_dir / f"{self.path.stem}.parquet"
        meta_path = self.cache_dir / f"{self.path.stem}.meta.json"
        return cache_path, meta_path

    def _cache_is_fresh(self, meta_path: Path) -> bool:
        if not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        stat = self.path.stat()
        return meta.get("source_size") == stat.st_size and meta.get("source_mtime") == stat.st_mtime

    def _write_cache(self, df: pd.DataFrame, cache_path: Path, meta_path: Path) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path)
        stat = self.path.stat()
        meta_path.write_text(
            json.dumps(
                {
                    "source_path": str(self.path),
                    "source_size": stat.st_size,
                    "source_mtime": stat.st_mtime,
                }
            ),
            encoding="utf-8",
        )

    def _fetch_xes_cached(self) -> pd.DataFrame:
        cache_path, meta_path = self._cache_paths()
        if self._cache_is_fresh(meta_path):
            return pd.read_parquet(cache_path)

        # Imported here, not at module level, so importing this module never
        # requires pm4py for callers only reading CSV/XLSX/Parquet.
        import pm4py

        df = pm4py.read_xes(str(self.path))
        self._write_cache(df, cache_path, meta_path)
        return df

    # --- CSV, with delimiter/encoding sniffing --------------------------

    def _fetch_csv(self) -> pd.DataFrame:
        encoding = self._sniff_encoding()
        delimiter = self._sniff_delimiter(encoding)
        return pd.read_csv(self.path, encoding=encoding, sep=delimiter)

    def _sniff_encoding(self) -> str:
        for candidate in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with self.path.open(encoding=candidate) as f:
                    f.read(65536)
                return candidate
            except UnicodeDecodeError:
                continue
        return "latin-1"  # latin-1 never raises; last resort

    def _sniff_delimiter(self, encoding: str) -> str:
        with self.path.open(encoding=encoding, newline="") as f:
            sample = f.read(65536)
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            return ","
