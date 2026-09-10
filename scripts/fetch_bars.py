"""Fetch daily bars for the candidate tickers and a wide neighbourhood universe.

Window is bounded by the experiment, not by history: 60 trailing sessions before
the first fire (for the correlation/volatility baseline) through 10 sessions
after the last (for the forward label). That is months of data, not years.

The neighbourhood universe is selected by liquidity from the data itself rather
than hand-picked, so D-27 ("wide universe, never the signal's own tickers") is
satisfied without a holdings file.

Usage:  uv run python scripts/fetch_bars.py
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest

FIRES = "data/fires.csv"
OUT = "data/bars.parquet"
UNIVERSE_OUT = "data/universe.csv"

PAD_BEFORE = 130  # calendar days ≈ 90 sessions, comfortably over the 60 needed
PAD_AFTER = 25    # calendar days ≈ 17 sessions, over the 10-session label
MIN_DOLLAR_VOL = 10_000_000  # median daily $ volume for universe membership
BATCH = 200


def client() -> StockHistoricalDataClient:
    try:
        return StockHistoricalDataClient(
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
        )
    except KeyError as e:
        sys.exit(f"missing env var {e}")


def liquid_symbols() -> list[str]:
    tc = TradingClient(
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True
    )
    assets = tc.get_all_assets(GetAssetsRequest(status="active", asset_class="us_equity"))
    syms = sorted(
        a.symbol for a in assets
        if a.tradable and a.exchange is not None
        and a.exchange.value in {"NYSE", "NASDAQ", "ARCA"}
        and "." not in a.symbol and "/" not in a.symbol
    )
    print(f"  active tradable US equities on NYSE/NASDAQ/ARCA: {len(syms)}")
    return syms


def fetch(cl: StockHistoricalDataClient, syms: list[str], start, end) -> pd.DataFrame:
    frames = []
    for i in range(0, len(syms), BATCH):
        chunk = syms[i : i + BATCH]
        req = StockBarsRequest(
            symbol_or_symbols=chunk,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment="all",  # splits, dividends, spin-offs (spike 04)
        )
        df = cl.get_stock_bars(req).df
        if not df.empty:
            frames.append(df)
        print(f"  batch {i // BATCH + 1}/{-(-len(syms) // BATCH)}: "
              f"{len(chunk)} symbols, {0 if df.empty else len(df)} bars", flush=True)
    return pd.concat(frames) if frames else pd.DataFrame()


def main() -> None:
    fires = pd.read_csv(FIRES)
    fires["posted_at"] = pd.to_datetime(fires.posted_at, utc=True, format="ISO8601")
    start = fires.posted_at.min() - timedelta(days=PAD_BEFORE)
    end = min(
        fires.posted_at.max() + timedelta(days=PAD_AFTER),
        pd.Timestamp(datetime.now(tz=UTC)),
    )
    print(f"window: {start.date()} -> {end.date()}")

    cl = client()
    syms = liquid_symbols()

    print("fetching bars...")
    bars = fetch(cl, syms, start, end)
    if bars.empty:
        sys.exit("no bars returned")

    bars = bars.reset_index()
    bars["dollar_vol"] = bars["close"] * bars["volume"]
    med = bars.groupby("symbol")["dollar_vol"].median()
    keep = set(med[med >= MIN_DOLLAR_VOL].index) | set(fires.ticker.unique())
    bars = bars[bars.symbol.isin(keep)]

    # Temp-file-then-rename, matching fetch_daily_bars.py. `node_news` reads
    # bars.parquet in the same LangGraph superstep that this node writes it, so
    # a direct write leaves a window where the reader sees a truncated file.
    # os.replace is atomic within a filesystem, which closes that window.
    # It does NOT fix the ordering -- news still reads whatever was there
    # before this write lands, which is the previous run's file. That is a
    # graph-edge problem, not a write problem.
    tmp = f"{OUT}.tmp"
    bars.to_parquet(tmp, index=False)
    os.replace(tmp, OUT)
    pd.Series(sorted(keep)).to_csv(UNIVERSE_OUT, index=False, header=["symbol"])

    print(f"\nwrote {OUT}")
    print(f"  universe: {len(keep)} symbols (median $vol >= ${MIN_DOLLAR_VOL:,} or a candidate)")
    lo, hi = bars.timestamp.min().date(), bars.timestamp.max().date()
    print(f"  bars: {len(bars):,} rows, {lo} -> {hi}")
    missing = set(fires.ticker.unique()) - set(bars.symbol.unique())
    print(f"  candidate tickers missing bars: {sorted(missing) if missing else 'none'}")


if __name__ == "__main__":
    main()
