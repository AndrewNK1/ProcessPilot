r"""Cut the committed BPI 2019 sample (``data/sample/bpi2019_sample.csv``).

WHY a sample in the repo: the end-to-end tests and a first run of the app
need a real purchase-to-pay log that is small (seconds to mine) and present
on every machine, including the supervisor's. The full 728 MB XES stays
outside the repo.

Selection is deterministic but not "the first N cases": per item category,
cases are ordered by the SHA-256 of their id and the first ``--per-segment``
are kept, which spreads the sample over the whole time range. Only cases that
survive the preset's suggested cleaning are eligible, so the sample has no
1948/2070 junk dates. Every raw event of a chosen case is kept (batch
postings included, so collapsing is exercised); only the five mapped columns
are written.

Run (PowerShell, needs PP_BPI2019_PATH in .env):
    .\.venv\python.exe scripts\make_bpi_sample.py
"""

from __future__ import annotations

import argparse
import hashlib

from processpilot.config import find_project_root, load_env, resolve_source_path
from processpilot.ingest.mapping import load_preset, split_by_segment
from processpilot.pipeline import (
    clean_log,
    load_canonical,
    suggested_cleaning_policy,
    validate_log,
)
from processpilot.sources.file_source import FileSource

OUT = "data/sample/bpi2019_sample.csv"


def main() -> None:
    """Write the sample CSV and print what it contains."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-segment", type=int, default=200)
    args = parser.parse_args()

    load_env()
    mapping, preset = load_preset("bpi2019")
    source = FileSource(resolve_source_path(preset))
    raw = source.fetch()
    canonical = load_canonical(source, mapping)
    clean, _ = clean_log(
        canonical, validate_log(canonical, preset), suggested_cleaning_policy(preset), preset
    )

    chosen: list[str] = []
    for name, seg in sorted(split_by_segment(clean).items()):
        ids = sorted(seg["case_id"].unique(), key=lambda c: hashlib.sha256(c.encode()).hexdigest())
        chosen += ids[: args.per_segment]
        print(f"{name:34s} {min(len(ids), args.per_segment):4d} of {len(ids):,} cases")

    columns = mapping.mapped_columns()
    sample = raw.loc[raw[mapping.case_id].astype(str).isin(set(chosen)), columns]
    sample = sample.sort_values([mapping.case_id, mapping.timestamp], kind="stable")
    out = find_project_root() / OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(out, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
    print(f"{len(sample):,} events, {len(chosen)} cases -> {out}")


if __name__ == "__main__":
    main()
