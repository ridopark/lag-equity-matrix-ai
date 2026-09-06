"""Freeze the inputs check_baseline.py needs, so a clone can run the guard.

Answers Q-27. `capture_baseline.rows()` rebuilds the assessment table from
`data/bars.parquet` (17 MB of Alpaca bars) and `data/fires.csv` (the real
alert feed) — neither is committed, so the guard's *actual* side could not be
regenerated outside this machine. This writes the two projections that are:

  tests/fixtures/closes.parquet  close only, every symbol, every session.
  tests/fixtures/fires.csv       ticker/posted_at/direction only.

Both are projections, not samples. The whole universe is kept because
`graph_retriever` ranks each candidate against it to pick neighbours — slicing
to the candidates' own symbols would silently change every neighbourhood. The
fires projection drops signal_id, author, strike and premium; the three columns
that remain are already public in data/baseline-98.csv.

Usage:  uv run python scripts/build_fixture.py
"""

from __future__ import annotations

import csv
import pathlib

import pandas as pd

BARS = "data/bars.parquet"
FIRES = "data/fires.csv"
OUT = pathlib.Path("tests/fixtures")

# Kept in float64: float32 costs 2.3e-4 on a close, which is enough to reorder
# a correlation rank and flip a verdict, defeating a byte-exact guard for 8%.
CLOSES_PARQUET = OUT / "closes.parquet"
FIRES_CSV = OUT / "fires.csv"
FIRES_COLUMNS = ["ticker", "posted_at", "direction"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    closes = pd.read_parquet(BARS).pivot_table(
        index="timestamp", columns="symbol", values="close"
    )
    closes.to_parquet(CLOSES_PARQUET, compression="zstd", compression_level=9)

    with open(FIRES) as fh, FIRES_CSV.open("w", newline="") as out:
        w = csv.DictWriter(out, fieldnames=FIRES_COLUMNS)
        w.writeheader()
        n = 0
        for row in csv.DictReader(fh):
            w.writerow({c: row[c] for c in FIRES_COLUMNS})
            n += 1

    print(f"{CLOSES_PARQUET}: {closes.shape[0]} sessions x {closes.shape[1]} symbols "
          f"({CLOSES_PARQUET.stat().st_size / 1e6:.2f} MB)")
    print(f"{FIRES_CSV}: {n} rows ({FIRES_CSV.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
