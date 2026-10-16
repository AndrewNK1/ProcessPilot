"""Integration test: the full Phase 1 ingest path on the real BPI 2019 log.

Opt-in (``pytest -m bpi2019``) because it needs the ~700 MB XES at
``PP_BPI2019_PATH``. The expected numbers were measured on that file in Phase
1; if the file changes, these assertions -- and the preset's STATUS note --
must be re-verified together.
"""

from __future__ import annotations

import pytest

from processpilot.config import load_env, resolve_source_path
from processpilot.ingest.mapping import apply_mapping, load_preset, split_by_segment
from processpilot.ingest.validate import validate
from processpilot.schema import TIMESTAMP_DTYPE
from processpilot.sources.file_source import FileSource

pytestmark = pytest.mark.bpi2019


@pytest.fixture(scope="module")
def bpi_canonical() -> tuple:
    load_env()
    mapping, preset = load_preset("bpi2019")
    try:
        path = resolve_source_path(preset)
    except (OSError, FileNotFoundError) as exc:
        pytest.skip(f"BPI 2019 log not available: {exc}")
    return apply_mapping(FileSource(path).fetch(), mapping), preset


def test_bpi2019_canonical_shape(bpi_canonical: tuple) -> None:
    df, _ = bpi_canonical
    assert len(df) == 1_595_923
    assert df["case_id"].nunique() == 251_734
    assert df["activity"].nunique() == 42
    assert df["timestamp"].dtype == TIMESTAMP_DTYPE
    assert "timestamp_raw" not in df.columns  # every XES timestamp converts


def test_bpi2019_segments_match_preset(bpi_canonical: tuple) -> None:
    df, _ = bpi_canonical
    cases = {name: seg["case_id"].nunique() for name, seg in split_by_segment(df).items()}
    assert cases == {
        "3-way match, invoice before GR": 221_010,
        "3-way match, invoice after GR": 15_182,
        "Consignment": 14_498,
        "2-way match": 1_044,
    }


def test_bpi2019_flags_junk_timestamps(bpi_canonical: tuple) -> None:
    df, preset = bpi_canonical
    cfg = preset["validation"]
    report = validate(
        df,
        plausible_timestamp_min=cfg["plausible_timestamp_min"],
        plausible_timestamp_max=cfg["plausible_timestamp_max"],
    )
    codes = {issue.code: issue for issue in report.issues}
    assert codes["timestamp_out_of_plausible_range"].count == 95
    assert "null_timestamp" not in codes
    assert "non_monotonic_timestamp" not in codes
