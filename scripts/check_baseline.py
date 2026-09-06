"""Executable form of success criterion 4 (PLAN-2026-09-03, TASK-0.3).

Regenerates the assessment baseline and diffs it against the committed
data/baseline-98.csv. Exits non-zero on any difference. Run at every phase
boundary of the LangGraph refactor.

Usage:  uv run python scripts/check_baseline.py
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from capture_baseline import (  # noqa: E402
    COLUMNS,
    FIXTURE_CLOSES,
    FIXTURE_FIRES,
    rows,
)

BASELINE = "data/baseline-98.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fixture",
        action="store_true",
        help="regenerate from the committed tests/fixtures/ projections rather than "
             "data/bars.parquet and data/fires.csv, which are not in the repo. "
             "Off by default on purpose: a silent fallback would let this report "
             "'unchanged' while the real inputs were missing or broken.",
    )
    args = ap.parse_args()

    try:
        with open(BASELINE) as fh:
            expected = list(csv.DictReader(fh))
    except FileNotFoundError:
        print(f"{BASELINE} not found — run scripts/capture_baseline.py first", file=sys.stderr)
        return 2

    try:
        actual = rows(FIXTURE_CLOSES, FIXTURE_FIRES) if args.fixture else rows()
    except FileNotFoundError as e:
        print(f"{e.filename} not found.", file=sys.stderr)
        if not args.fixture:
            print("  This input is not committed (Q-27). Either fetch it with "
                  "scripts/fetch_bars.py, or run with --fixture to use the "
                  "committed projections.", file=sys.stderr)
        return 2
    if len(actual) != len(expected):
        print(f"ROW COUNT CHANGED: baseline {len(expected)}, now {len(actual)}", file=sys.stderr)
        return 1

    diffs = []
    for e, a in zip(expected, actual, strict=True):
        for col in COLUMNS:
            if str(e[col]) != str(a[col]):
                diffs.append(
                    f"{a['as_of']} {a['symbol']} {a['direction']}: "
                    f"{col} {e[col]!r} -> {a[col]!r}"
                )
    if diffs:
        print(f"BASELINE DIFF on {len(diffs)} field(s):", file=sys.stderr)
        for d in diffs[:20]:
            print(f"  {d}", file=sys.stderr)
        if len(diffs) > 20:
            print(f"  ... and {len(diffs) - 20} more", file=sys.stderr)
        return 1

    print(f"baseline unchanged: {len(actual)} rows identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
