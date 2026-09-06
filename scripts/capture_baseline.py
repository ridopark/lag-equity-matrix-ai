"""Capture the pre-refactor assessment baseline (PLAN-2026-09-03, TASK-0.1).

Freezes what the pipeline produces for all 98 candidates so the LangGraph
refactor can be proven behaviour-preserving. The criterion is *unchanged*, not
*correct*.

One row per **candidate**, not per assessment. Candidates the pipeline drops
before the assessor (no Alpaca bars, insufficient history) get an explicit
`no_assessment` verdict rather than silently vanishing — otherwise a refactor
that drops a *different* candidate would leave the row count intact and slip
through. Absence has to be a value to be diffable (CLAUDE.md: unverifiable means
badly designed).

Usage:  uv run python scripts/capture_baseline.py [--out data/baseline-98.csv]
        uv run python scripts/capture_baseline.py --synthetic  (regenerates the
            committed synthetic golden file; clone-runnable, see D-52)
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
from collections import Counter

import pandas as pd

from lagmatrix.adapters.candidates import ExternalSignals
from lagmatrix.pipeline.runner import run_sync

COLUMNS = [
    "symbol", "as_of", "direction", "verdict",
    "effective_evidence", "n_supporting", "n_contradicting",
]


# The real inputs are not committed (vendor bars, private alert feed). The
# synthetic universe in tests/fixtures/ is, and exercises the same code paths --
# see scripts/make_synthetic.py and D-52.
BARS_PATH = "data/bars.parquet"
FIRES_PATH = "data/fires.csv"
SYNTHETIC_CLOSES = "tests/fixtures/synthetic-closes.parquet"
SYNTHETIC_FIRES = "tests/fixtures/synthetic-fires.csv"
SYNTHETIC_BASELINE = "tests/fixtures/synthetic-baseline.csv"


def rows(bars_path: str = BARS_PATH, fires_path: str = FIRES_PATH) -> list[dict]:
    if bars_path.endswith(".parquet") and "closes" in bars_path:
        closes = pd.read_parquet(bars_path)  # already pivoted
    else:
        bars = pd.read_parquet(bars_path)
        closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")

    # One source, passed to run_sync too: run() would otherwise build its own
    # from the default path and reach data/fires.csv, which a clone does not have.
    signals = ExternalSignals(fires_path)
    candidates = signals.candidates()
    # publisher prints per assessment; that noise makes the evidence output
    # unreadable, so swallow it here (the pipeline's own behaviour is unchanged)
    with contextlib.redirect_stdout(io.StringIO()):
        assessments, _thread_id, _interrupt = run_sync(
            closes=closes, with_news=False, limit=None, signals=signals
        )

    out = [
        {
            "symbol": a.candidate.symbol,
            "as_of": a.candidate.as_of.isoformat(),
            "direction": a.candidate.direction,
            "verdict": a.verdict,
            # 6 dp so float formatting cannot cause a spurious diff
            "effective_evidence": f"{a.effective_evidence:.6f}",
            "n_supporting": len(a.supporting),
            "n_contradicting": len(a.contradicting),
        }
        for a in assessments
    ]

    # Candidates that produced no assessment become explicit rows.
    assessed = Counter(
        (a.candidate.symbol, a.candidate.as_of.isoformat(), a.candidate.direction)
        for a in assessments
    )
    submitted = Counter(
        (c.symbol, c.as_of.isoformat(), c.direction) for c in candidates
    )
    for key, n in submitted.items():
        for _ in range(n - assessed.get(key, 0)):
            out.append(
                {
                    "symbol": key[0], "as_of": key[1], "direction": key[2],
                    "verdict": "no_assessment", "effective_evidence": f"{0.0:.6f}",
                    "n_supporting": 0, "n_contradicting": 0,
                }
            )

    # Sort on the full row: duplicate (symbol, as_of, direction) candidates are
    # then ordered by content, so Send's nondeterministic fan-out order in
    # PHASE-2 cannot produce a spurious diff.
    return sorted(out, key=lambda r: tuple(str(r[c]) for c in COLUMNS))


def write(path: str, data: list[dict]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(data)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/baseline-98.csv")
    ap.add_argument("--synthetic", action="store_true",
                    help="run against the committed synthetic universe instead of data/")
    args = ap.parse_args()
    if args.synthetic:
        data = rows(SYNTHETIC_CLOSES, SYNTHETIC_FIRES)
        if args.out == "data/baseline-98.csv":
            args.out = SYNTHETIC_BASELINE
    else:
        data = rows()
    write(args.out, data)
    print(f"wrote {args.out}: {len(data)} data rows")


if __name__ == "__main__":
    main()
