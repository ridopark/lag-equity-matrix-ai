"""RED for `lagmatrix.ingest` (PHASE-3, PLAN-2026-09-09-ingest-coherence):
the two pure functions an incremental `data/bars-10y.parquet` extension rests
on. This completes `PLAN-2026-09-09-daily-ingest.md` PHASE-2's spec, which
named `merge_bars`/`daily_watermark` but was never built (`src/lagmatrix
/ingest.py` did not exist).

Frame shape matches `data/bars-10y.parquet` itself (checked directly this
session): `symbol` (str), `timestamp` (datetime64[us, UTC]), `close`
(float64), `volume` (float64), on a plain `RangeIndex` -- not the wider
`data/bars.parquet` shape, since PHASE-3 extends the long file specifically.
No parquet file is read here; fixtures are built by hand.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from lagmatrix.ingest import daily_watermark, merge_bars


def _bars(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    """A small frame in `bars-10y.parquet`'s own column shape."""
    df = pd.DataFrame(rows, columns=["symbol", "timestamp", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def test_merge_bars_dedupes_by_symbol_and_timestamp():
    """Falsifies if the merged frame keeps both rows for a shared
    (symbol, timestamp) key, or keeps the existing frame's `close` instead of
    the new fetch's -- the "fresher fetch wins" contract PHASE-3 quotes from
    the original PHASE-2 spec (matching Alpaca's own late-correction
    behaviour)."""
    existing = _bars(
        [
            ("AAPL", "2026-09-01", 100.0, 1000.0),
            ("AAPL", "2026-09-02", 101.0, 1100.0),
        ]
    )
    new = _bars(
        [
            ("AAPL", "2026-09-02", 999.0, 1100.0),  # corrected close
            ("AAPL", "2026-09-03", 103.0, 1300.0),
        ]
    )

    merged = merge_bars(existing, new)

    shared = merged[merged["timestamp"] == pd.Timestamp("2026-09-02", tz="UTC")]
    assert len(shared) == 1
    assert shared["close"].iloc[0] == 999.0
    assert len(merged) == 3


def test_merge_bars_is_a_noop_on_identical_rerun():
    """Falsifies if re-merging identical existing/new frames changes row
    count or values -- the idempotency contract the whole nightly job rests
    on (D-97: loaders/ingest never silently duplicate on a rerun)."""
    df = _bars(
        [
            ("AAPL", "2026-09-01", 100.0, 1000.0),
            ("MSFT", "2026-09-01", 200.0, 2000.0),
        ]
    )

    merged = merge_bars(df, df)

    assert len(merged) == len(df)
    pd.testing.assert_frame_equal(
        merged.sort_values(["symbol", "timestamp"]).reset_index(drop=True),
        df.sort_values(["symbol", "timestamp"]).reset_index(drop=True),
    )


def test_daily_watermark_is_the_max_timestamp_present():
    """Falsifies if the watermark is not the single max timestamp across the
    whole file -- the contract `fetch_daily_bars.py`'s `--since` is built
    from."""
    df = _bars(
        [
            ("AAPL", "2026-09-01", 100.0, 1000.0),
            ("MSFT", "2026-09-03", 200.0, 2000.0),
            ("AAPL", "2026-09-02", 101.0, 1100.0),
        ]
    )

    assert daily_watermark(df) == pd.Timestamp("2026-09-03", tz="UTC").date()


def test_daily_watermark_on_empty_frame_returns_none():
    """An empty frame's own `.timestamp.max()` is `NaT` (confirmed directly:
    `pd.Series(dtype="datetime64[us, UTC]").max()` is `NaT`, not a raised
    exception) -- but the first-ever-run caller needs `None`, not `NaT`, to
    build `watermark + 1 day` without special-casing a pandas sentinel.
    Falsifies if `daily_watermark` returns `NaT`, raises, or returns anything
    other than `None` on an empty frame."""
    empty = _bars([])

    result = daily_watermark(empty)

    assert result is None


def test_split_affected_symbols_finds_a_forward_split():
    """Falsifies if a symbol under `forward_splits` is missing from the
    result, or the function fails to import at all (it does not yet exist --
    PHASE-3 TASK-3.3/3.4, PLAN-2026-09-09-ingest-coherence, revised). Fixture
    uses `SimpleNamespace` for attribute access, matching the real SDK: the
    live response is confirmed non-subscriptable (`row["symbol"]` raises
    `TypeError: 'ForwardSplit' object is not subscriptable`; `row.symbol`
    works). A dict-of-dicts fixture would let a `row["symbol"]`
    implementation pass here and raise `TypeError` on the first real split --
    the plan's original TASK-3.3 spec was wrong on this point; corrected."""
    from lagmatrix.ingest import split_affected_symbols

    actions_data = {
        "forward_splits": [
            SimpleNamespace(
                corporate_action_type="forward_splits",
                symbol="NVDA",
                cusip="67066G104",
                new_rate=10.0,
                old_rate=1.0,
                process_date=date(2024, 6, 10),
                ex_date=date(2024, 6, 10),
            )
        ]
    }

    assert split_affected_symbols(actions_data) == {"NVDA"}


def test_split_affected_symbols_finds_a_reverse_and_unit_split():
    """Falsifies if either symbol is missing -- `reverse_splits` reads the
    same `symbol` field as forward splits, but `unit_splits` has no `symbol`
    field at all (confirmed against the SDK's `UnitSplit` model: it carries
    `old_symbol`/`new_symbol` instead); a naive implementation that reads
    `.symbol` off every entry uniformly would raise `AttributeError` on the
    unit-split entry, not merely drop it."""
    from lagmatrix.ingest import split_affected_symbols

    actions_data = {
        "reverse_splits": [
            SimpleNamespace(
                corporate_action_type="reverse_splits",
                symbol="XYZ",
                new_rate=1.0,
                old_rate=10.0,
            ),
        ],
        "unit_splits": [
            SimpleNamespace(
                corporate_action_type="unit_splits",
                old_symbol="ABC",
                new_symbol="ABC",
                old_rate=1.0,
                new_rate=0.5,
            ),
        ],
    }

    assert split_affected_symbols(actions_data) == {"XYZ", "ABC"}


def test_split_affected_symbols_ignores_other_action_types():
    """Falsifies if a symbol appearing only under a non-split action type
    (here `cash_dividends`) leaks into the result -- the plan's stated scope
    boundary (splits only; dividends/spin-offs explicitly out). This fixture
    also has no `forward_splits`/`reverse_splits`/`unit_splits` key at all,
    matching the live response verified against Alpaca (only keys with
    results are present), so it doubles as the missing-key case: the
    function must not raise on a key that is simply absent."""
    from lagmatrix.ingest import split_affected_symbols

    actions_data = {
        "cash_dividends": [
            SimpleNamespace(corporate_action_type="cash_dividends", symbol="KO", rate=0.48),
        ]
    }

    assert split_affected_symbols(actions_data) == set()


def test_split_affected_symbols_on_empty_response_is_empty():
    """Falsifies if an empty dict (the common, no-splits-tonight case) raises
    or returns anything other than an empty set."""
    from lagmatrix.ingest import split_affected_symbols

    assert split_affected_symbols({}) == set()
