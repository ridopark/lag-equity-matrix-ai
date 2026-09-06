"""Executable form of success criterion 4 (PLAN-2026-09-03, TASK-0.3).

Regenerates the assessment baseline and diffs it against the committed
data/baseline-98.csv. Exits non-zero on any difference. Run at every phase
boundary of the LangGraph refactor.

Usage:  uv run python scripts/check_baseline.py
"""

from __future__ import annotations

import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from capture_baseline import COLUMNS, rows  # noqa: E402

BASELINE = "data/baseline-98.csv"


def main() -> int:
    try:
        with open(BASELINE) as fh:
            expected = list(csv.DictReader(fh))
    except FileNotFoundError:
        print(f"{BASELINE} not found — run scripts/capture_baseline.py first", file=sys.stderr)
        return 2

    actual = rows()
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
