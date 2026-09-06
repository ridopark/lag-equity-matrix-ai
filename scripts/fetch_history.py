"""Fetch multi-year daily bars for the synthetic-candidate test (D-63).

Separate from `fetch_bars.py`, which pulls a short window around the signal
feed's dates. This pulls a long window for a fixed liquid universe so the
shipped feature can be evaluated on ~1,200 trading dates rather than 97.

Universe caveat, stated in D-63 and repeated here because it is easy to forget:
liquidity is measured on the recent window, so the constituent list carries
survivorship and look-ahead. Delisted names are absent entirely.

Usage:  uv run python scripts/fetch_history.py [--years 5] [--out data/bars-5y.parquet]
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import UTC, datetime, timedelta

import pandas as pd
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

BATCH = 200
EXCLUDED = "data/excluded-etfs.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--out", default="data/bars-5y.parquet")
    args = ap.parse_args()

    recent = pd.read_parquet("data/bars.parquet")
    with open(EXCLUDED) as fh:
        funds = {r["symbol"] for r in csv.DictReader(fh)}

    dv = recent.groupby("symbol").dollar_vol.median().sort_values(ascending=False)
    universe = [s for s in dv.index if s not in funds]
    print(f"  universe: {len(universe)} non-fund names (from {len(dv)} liquid symbols)")

    end = datetime.now(UTC) - timedelta(days=1)
    start = end - timedelta(days=365 * args.years + 5)
    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                       os.environ["ALPACA_SECRET_KEY"])
    frames = []
    for i in range(0, len(universe), BATCH):
        chunk = universe[i:i + BATCH]
        r = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
            start=start, end=end, adjustment="all", feed="sip"))
        df = r.df
        if df is None or df.empty:
            continue
        df = df.reset_index()[["symbol", "timestamp", "close", "volume"]]
        frames.append(df)
        print(f"    batch {i//BATCH + 1}/{-(-len(universe)//BATCH)}: "
              f"{df.symbol.nunique()} symbols, {len(df):,} bars")

    bars = pd.concat(frames, ignore_index=True)
    bars.to_parquet(args.out, compression="zstd", compression_level=6)
    print(f"  wrote {args.out}: {len(bars):,} rows, {bars.symbol.nunique()} symbols, "
          f"{bars.timestamp.min().date()} -> {bars.timestamp.max().date()}, "
          f"{os.path.getsize(args.out)/1e6:.0f} MB")


if __name__ == "__main__":
    main()
