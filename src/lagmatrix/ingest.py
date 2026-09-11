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


def plan_embeddings(
    candidate_ids: list, existing_keys: set[str]
) -> tuple[int, int, list[str]]:
    """The key-set anti-join at the centre of Q-56 (`docs/spikes/overall.md`):
    `candidate_ids` (postgres `id`s -- `int`) against `existing_keys`
    (ArangoDB `_key`s -- always `str`). Coercing to `str` here, not leaving it
    to the caller, is what stops a type mismatch from finding zero overlap
    and re-embedding the whole corpus every night.

    Returns `(n_candidates, n_already_embedded, to_embed)`. `to_embed` is
    plain lexicographic `sorted()` (matching `load_vectors.py`'s own id
    convention), not a numeric sort of the underlying ints."""
    ids = {str(c) for c in candidate_ids}
    n_already_embedded = len(ids & existing_keys)
    to_embed = sorted(ids - existing_keys)
    return len(candidate_ids), n_already_embedded, to_embed


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
