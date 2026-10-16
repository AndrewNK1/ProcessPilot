"""Unit tests for processpilot.sources.file_source.

The XES cache is tested with pm4py's parser replaced by a counting stub: the
behaviour under test is *when* the cache is trusted, not XES parsing itself
(which is pm4py's job and covered by test_environment).
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from processpilot.sources.file_source import FileSource


@pytest.fixture
def fake_read_xes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace ``pm4py.read_xes`` with a stub; returns the list of parsed paths."""
    import pm4py

    calls: list[str] = []

    def _stub(path: str) -> pd.DataFrame:
        calls.append(path)
        return pd.DataFrame({"case:concept:name": ["c1"], "concept:name": [Path(path).name]})

    monkeypatch.setattr(pm4py, "read_xes", _stub)
    return calls


def _touch_xes(path: Path, content: str = "<log/>") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_rejects_unsupported_extension(tmp_path: Path) -> None:
    bad = tmp_path / "log.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        FileSource(bad)


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FileSource(tmp_path / "absent.csv")


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_csv_delimiter_is_sniffed(tmp_path: Path, delimiter: str) -> None:
    path = tmp_path / "log.csv"
    rows = ["case,activity,time", "c1,A,2019-01-01", "c1,B,2019-01-02"]
    path.write_text("\n".join(r.replace(",", delimiter) for r in rows), encoding="utf-8")
    df = FileSource(path).fetch()
    assert list(df.columns) == ["case", "activity", "time"]
    assert len(df) == 2


def test_csv_latin1_byte_after_first_64kb_is_decoded(tmp_path: Path) -> None:
    """Regression: encoding used to be chosen from a 64 KB prefix only."""
    path = tmp_path / "log.csv"
    filler = "".join(f"c{i},A,2019-01-01\n" for i in range(6000))  # > 64 KB of ASCII
    data = ("case,activity,time\n" + filler).encode("ascii") + "c9,Prüfung,2019-01-02\n".encode(
        "latin-1"
    )
    path.write_bytes(data)
    df = FileSource(path).fetch()
    assert df["activity"].iloc[-1] == "Prüfung"


def test_csv_utf8_bom_is_stripped(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text("case,activity\nc1,A\n", encoding="utf-8-sig")
    assert list(FileSource(path).fetch().columns) == ["case", "activity"]


def test_xes_second_fetch_is_served_from_cache(tmp_path: Path, fake_read_xes: list[str]) -> None:
    xes = _touch_xes(tmp_path / "log.xes")
    cache = tmp_path / "cache"
    FileSource(xes, cache_dir=cache).fetch()
    FileSource(xes, cache_dir=cache).fetch()
    assert len(fake_read_xes) == 1


def test_xes_cache_invalidated_when_source_changes(
    tmp_path: Path, fake_read_xes: list[str]
) -> None:
    xes = _touch_xes(tmp_path / "log.xes")
    cache = tmp_path / "cache"
    FileSource(xes, cache_dir=cache).fetch()
    _touch_xes(xes, "<log>changed</log>")
    stat = xes.stat()
    os.utime(xes, (stat.st_atime, stat.st_mtime + 10))
    FileSource(xes, cache_dir=cache).fetch()
    assert len(fake_read_xes) == 2


def test_xes_files_with_same_name_in_different_folders_do_not_share_cache(
    tmp_path: Path, fake_read_xes: list[str]
) -> None:
    """Regression: the cache used to be keyed on the file stem alone."""
    a = _touch_xes(tmp_path / "a" / "log.xes")
    b = _touch_xes(tmp_path / "b" / "log.xes")
    os.utime(b, (a.stat().st_atime, a.stat().st_mtime))  # same size AND mtime
    cache = tmp_path / "cache"
    FileSource(a, cache_dir=cache).fetch()
    FileSource(b, cache_dir=cache).fetch()
    assert len(fake_read_xes) == 2


def test_xes_cache_with_deleted_parquet_is_rebuilt(
    tmp_path: Path, fake_read_xes: list[str]
) -> None:
    """Regression: a surviving .meta.json with no .parquet used to crash."""
    xes = _touch_xes(tmp_path / "log.xes")
    cache = tmp_path / "cache"
    FileSource(xes, cache_dir=cache).fetch()
    for parquet in cache.glob("*.parquet"):
        parquet.unlink()
    df = FileSource(xes, cache_dir=cache).fetch()
    assert len(fake_read_xes) == 2
    assert len(df) == 1


def test_describe_reports_provenance(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text("case,activity\nc1,A\nc1,B\n", encoding="utf-8")
    info = FileSource(path).describe()
    assert info["type"] == "file"
    assert info["format"] == "csv"
    assert info["row_count"] == 2
