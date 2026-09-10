"""Pure merge helpers for incrementally extending `data/bars-10y.parquet`
(PHASE-3, PLAN-2026-09-09-ingest-coherence).

Both functions take frames and return values -- no I/O, no parquet reads.
The frame shape matches `bars-10y.parquet` itself: `symbol` (str), `timestamp`
(datetime64[us, UTC]), `close` (float64), `volume` (float64).
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def merge_bars(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge `new` bars into `existing`, the new fetch winning on a shared
    (symbol, timestamp) key -- matching Alpaca's own late-correction
    behaviour. Idempotent: `merge_bars(df, df) == df` (D-97)."""
    merged = pd.concat([existing, new]).drop_duplicates(
        subset=["symbol", "timestamp"], keep="last"
    )
    return merged.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def daily_watermark(df: pd.DataFrame) -> date | None:
    """The latest timestamp present in `df`, as a `date`; `None` on an empty
    frame (`.max()` on an empty series is `NaT`, not a raised exception)."""
    if df.empty:
        return None
    return df["timestamp"].max().date()


def split_affected_symbols(actions_data: dict) -> set[str]:
    """Symbols touched by a split in an already-fetched corporate actions
    response (splits only -- `cash_dividends` and other action types
    contribute nothing). `forward_splits`/`reverse_splits` carry `symbol`;
    `unit_splits` instead carries `old_symbol`/`new_symbol`. Only keys with
    results are present in the response, so each lookup uses `.get(name, [])`."""
    symbols: set[str] = set()
    for row in actions_data.get("forward_splits", []):
        symbols.add(row.symbol)
    for row in actions_data.get("reverse_splits", []):
        symbols.add(row.symbol)
    for row in actions_data.get("unit_splits", []):
        symbols.add(row.old_symbol)
        symbols.add(row.new_symbol)
    return symbols
