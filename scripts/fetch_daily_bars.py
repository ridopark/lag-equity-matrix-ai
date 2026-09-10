"""Extend `data/bars-10y.parquet` by the sessions it is missing (PHASE-3,
PLAN-2026-09-09-ingest-coherence).

Incremental by trading-session date across the whole file, not per symbol: one
universe arrives on one calendar. The exception is a split. A split moves a
symbol's whole adjustment basis, so appending fresh `adjustment="all"` rows to
history fetched on the pre-split basis would leave that symbol's series with a
step at the join. Symbols with a split reported in `[watermark - 5 days, today]`
are therefore refetched in full and merged over their stale rows -- `merge_bars`
keeps the last of a duplicated (symbol, timestamp), and the refetch is
concatenated after the existing frame, so the fresh rows win.

The refetch is reported, never silent: a symbol named here is being repaired,
and it will be named again for the few nightly runs it takes the query window's
lower bound to pass the split's date. That is expected and self-limiting.

Usage:  uv run python scripts/fetch_daily_bars.py [--out data/bars-10y.parquet] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from lagmatrix.ingest import daily_watermark, merge_bars, split_affected_symbols

BATCH = 200
COLUMNS = ["symbol", "timestamp", "close", "volume"]
SPLIT_PAD = 5  # calendar days back from the watermark, for a late-reported split
SPLIT_TYPES = ["forward_split", "reverse_split", "unit_split"]  # singular in the request


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def fetch(cl: StockHistoricalDataClient, syms: list[str], start: date, end: date) -> pd.DataFrame:
    """Daily bars for `syms`, batched like every other bars fetch here.

    `adjustment="all"` and `feed="sip"` match `fetch_history.py`, which wrote the
    file being extended: a different basis would append rows that do not belong
    to the series they are extending.
    """
    frames = []
    for i in range(0, len(syms), BATCH):
        chunk = syms[i : i + BATCH]
        df = cl.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
            start=_utc(start), end=_utc(end), adjustment="all", feed="sip")).df
        if df is None or df.empty:
            continue
        frames.append(df.reset_index()[COLUMNS])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)


def splits(ca: CorporateActionsClient, syms: list[str], start: date, end: date) -> set[str]:
    """Symbols with a split reported in `[start, end]`, batched like the bars."""
    affected: set[str] = set()
    for i in range(0, len(syms), BATCH):
        r = ca.get_corporate_actions(CorporateActionsRequest(
            symbols=syms[i : i + BATCH], types=SPLIT_TYPES, start=start, end=end))
        affected |= split_affected_symbols(r.data)
    return affected


def write(bars: pd.DataFrame, out: str) -> None:
    """Write via a temp file in the same directory, then rename: a crash
    mid-write leaves the previous file intact. It is a decade of vendor data."""
    tmp = f"{out}.tmp"
    bars.to_parquet(tmp, compression="zstd", compression_level=6)
    os.replace(tmp, out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/bars-10y.parquet")
    ap.add_argument("--dry-run", action="store_true", help="fetch and merge, but do not write")
    args = ap.parse_args()

    try:
        existing = pd.read_parquet(args.out)
    except FileNotFoundError:
        existing = pd.DataFrame(columns=COLUMNS)
    watermark = daily_watermark(existing)
    if watermark is None:
        sys.exit(f"{args.out} has no rows -- seed it with scripts/fetch_history.py first")

    today = datetime.now(UTC).date()
    symbols = sorted(existing["symbol"].unique())
    print(f"{args.out}: {len(existing):,} rows, {len(symbols)} symbols, through {watermark}")

    key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    ca_start = watermark - timedelta(days=SPLIT_PAD)
    affected = splits(CorporateActionsClient(key, secret), symbols, ca_start, today)
    # A unit split names a `new_symbol` that need not be in this file, and only a
    # symbol the file already carries has a stored start date to refetch from.
    affected &= set(symbols)
    print(f"split check: {ca_start} -> {today}, "
          f"{len(affected)} symbols refetched for a split"
          f"{': ' + ', '.join(sorted(affected)) if affected else ''}")

    cl = StockHistoricalDataClient(key, secret)
    frames = []
    for sym in sorted(affected):
        start = existing.loc[existing.symbol == sym, "timestamp"].min().date()
        full = fetch(cl, [sym], start, today)
        print(f"  {sym}: refetched in full from {start}, {len(full):,} bars")
        frames.append(full)

    others = [s for s in symbols if s not in affected]
    inc_start = watermark + timedelta(days=1)
    if inc_start > today:
        print(f"already current through {watermark}: 0 new rows")
    else:
        print(f"fetching {inc_start} -> {today} for {len(others)} symbols...")
        frames.append(fetch(cl, others, inc_start, today))

    # An empty fetch (a weekend run, a symbol with no bars) carries object dtypes;
    # dropping it keeps the merge on the stored file's own dtypes.
    frames = [f for f in frames if len(f)]
    new = pd.concat(frames, ignore_index=True) if frames else existing.iloc[:0]
    merged = merge_bars(existing, new)
    added = len(merged) - len(existing)

    if not args.dry_run and len(new):
        write(merged, args.out)
    verb = "dry run, not written" if args.dry_run else ("wrote" if len(new) else "unchanged")
    print(f"{verb} {args.out}: {len(merged):,} rows ({added:+,} new rows, "
          f"{len(new):,} fetched), {merged.symbol.nunique()} symbols, "
          f"{merged.timestamp.min().date()} -> {merged.timestamp.max().date()}, "
          f"{len(affected)} symbols refetched for a split"
          f"{': ' + ', '.join(sorted(affected)) if affected else ''}")


if __name__ == "__main__":
    main()
