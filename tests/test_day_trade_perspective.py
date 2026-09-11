"""RED for PHASE-2 (PLAN-2026-09-11-quant-daytrade-perspectives):
`compute_day_trade_perspective`, the deterministic day-trade node.

`DayTradePerspective` and `compute_day_trade_perspective` do not exist yet --
TASK-2.5 and TASK-2.6 belong to the green phase. These four tests pin what
the green implementation must satisfy:

* trailing liquidity (`median_dollar_vol`/`median_trade_count`) is computed
  only over sessions strictly before `candidate.as_of` (TASK-2.1, D-16) --
  never over the as_of session's own bar, even when one exists in `bars`;
* `gap_ratio` separates a symbol whose entire historical move happens
  overnight from one whose entire move happens intraday (TASK-2.2), and
  describes measured history only -- not a forecast (D-96);
* missing bars data -- `bars=None` or the candidate's symbol simply absent --
  yields liquidity fields of `None` with a stated reason, and the call
  completes rather than raising (TASK-2.3, D-38);
* `not_measurable` unconditionally lists `spread`, `slippage` and `borrow`,
  each with its own reason, on every call including the fully-populated
  happy path (TASK-2.4) -- there is no quote, fill or borrow-rate data
  anywhere in this pipeline (Q-33/Q-22), so the field exists precisely so
  that absence is stated, never implied by silence.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.nodes.day_trade_perspective import compute_day_trade_perspective


def _candidate(symbol: str, as_of: date, direction: str = "up") -> Candidate:
    return Candidate(symbol=symbol, direction=direction, as_of=as_of, origin="external")


def test_trailing_liquidity_medians_exclude_the_as_of_sessions_own_bar(bars_ohlcv):
    """TASK-2.1: `median_dollar_vol`/`median_trade_count` are computed over
    the trailing sessions strictly before `as_of` -- never the as_of
    session's own bar, even though `bars_ohlcv` deliberately includes one,
    carrying an extreme volume/trade_count (D-16).

    Both the correct (trailing-only) and the wrong (as_of-inclusive) medians
    are computed directly from the fixture with pandas below, rather than
    hardcoded, and shown to differ -- so the assertion that the result
    matches the former and not the latter is checked against real,
    independently-verified numbers.

    Falsifiable by: an implementation that includes the as_of session in its
    trailing window (e.g. slices `bars[bars.timestamp <= as_of]` instead of
    `<`) -- `result.median_dollar_vol`/`median_trade_count` would then equal
    `wrong_dv_median`/`wrong_tc_median` instead of the trailing-only values
    asserted below.
    """
    as_of = bars_ohlcv["timestamp"].max().date()
    candidate = _candidate(symbol="CAND", as_of=as_of)

    trailing_only = bars_ohlcv[bars_ohlcv.timestamp < pd.Timestamp(as_of, tz="UTC")]
    expected_dv_median = trailing_only.dollar_vol.median()
    expected_tc_median = trailing_only.trade_count.median()
    wrong_dv_median = bars_ohlcv.dollar_vol.median()
    wrong_tc_median = bars_ohlcv.trade_count.median()
    assert expected_dv_median != wrong_dv_median
    assert expected_tc_median != wrong_tc_median

    result = compute_day_trade_perspective(candidate, bars_ohlcv, trail=10)

    assert result.median_dollar_vol == expected_dv_median
    assert result.median_trade_count == expected_tc_median
    assert result.median_dollar_vol != wrong_dv_median
    assert result.median_trade_count != wrong_tc_median


def test_gap_ratio_distinguishes_an_all_gap_symbol_from_an_all_intraday_symbol():
    """TASK-2.2: `gap_ratio` separates a symbol whose entire historical move
    happens overnight (open jumps away from the prior session's close, flat
    within the session) from one whose entire move happens intraday (opens
    exactly at the prior close, moves during the session).

    ALLGAP: `high == low == open == close` every session (zero intraday
    range) -- the whole move is the opening gap.
    ALLINTRADAY: `open` equals the prior session's close exactly every
    session (zero gap) -- the whole move happens during the session.

    Both symbols use the same `moves` sequence, just relocated across the
    gap/intraday boundary, so this isolates *where* the move happens rather
    than *how much* moves. Verified directly with pandas below (this
    repo's own house rule, see `test_comovement_source.py`) rather than
    assumed, so the two extremes asserted at the end are known-sound before
    green exists.

    This describes measured history only -- it is not a forecast of
    tomorrow's open (D-96's caveat discipline); the implementation's
    docstring must say so.

    Falsifiable by: computing `gap_ratio` from anything that ignores the
    *prior* session's close (e.g. today's own high-low range alone) --
    ALLGAP and ALLINTRADAY would then report the same value instead of the
    two extremes below.
    """
    n_trail = 10
    sessions = pd.bdate_range("2026-02-01", periods=n_trail + 1, tz="UTC")
    seed, trail_sessions = sessions[0], sessions[1:]
    as_of = (trail_sessions[-1] + pd.tseries.offsets.BDay(1)).date()

    moves = np.array([1.0, -1.0, 1.5, -1.5, 2.0, -2.0, 1.0, -1.0, 1.5, -1.5])
    cum = 100.0 + np.cumsum(moves)
    prior = np.concatenate(([100.0], cum[:-1]))
    all_sessions = np.concatenate(([seed], trail_sessions))

    # ALLGAP: open == close == cum every session (flat); the jump from the
    # prior close into `open` carries the whole move.
    allgap_open = np.concatenate(([100.0], cum))
    allgap_close = allgap_open
    # ALLINTRADAY: open == prior close every session; close == cum carries
    # the whole move within the session.
    allintraday_open = np.concatenate(([100.0], prior))
    allintraday_close = np.concatenate(([100.0], cum))

    # Sanity-check the fixture actually engineers what this test needs,
    # computed directly rather than assumed.
    assert np.array_equal(allgap_open, allgap_close)  # zero intraday range
    assert np.array_equal(allintraday_open[1:], prior)  # zero overnight gap

    def _frame(symbol: str, opens: np.ndarray, closes: np.ndarray) -> pd.DataFrame:
        highs = np.maximum(opens, closes)
        lows = np.minimum(opens, closes)
        return pd.DataFrame({
            "symbol": symbol,
            "timestamp": all_sessions,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1_000,
            "trade_count": 100,
            "vwap": closes,
            "dollar_vol": closes * 1_000,
        })

    bars = pd.concat([
        _frame("ALLGAP", allgap_open, allgap_close),
        _frame("ALLINTRADAY", allintraday_open, allintraday_close),
    ], ignore_index=True)

    allgap_result = compute_day_trade_perspective(
        _candidate(symbol="ALLGAP", as_of=as_of), bars, trail=n_trail
    )
    allintraday_result = compute_day_trade_perspective(
        _candidate(symbol="ALLINTRADAY", as_of=as_of), bars, trail=n_trail
    )

    assert allgap_result.gap_ratio == 1.0
    assert allintraday_result.gap_ratio == 0.0


def test_bars_none_yields_liquidity_fields_none_with_a_stated_reason():
    """TASK-2.3: with no bars data at all (`bars=None`), liquidity is not
    measurable -- `median_dollar_vol`/`median_trade_count`/`gap_ratio` come
    back `None`, `note` states why, and the call returns normally rather
    than raising (D-38: a silent path gets an explicit outcome, not a crash
    and not a plausible-looking value).

    Falsifiable by: raising instead of returning (fails this test directly),
    returning 0.0 instead of `None`, or returning `None` with an empty
    `note` that never says why -- the sole return-a-None-with-no-explanation
    shape D-38 exists to catch.
    """
    candidate = _candidate(symbol="CAND", as_of=date(2026, 6, 1))

    result = compute_day_trade_perspective(candidate, None, trail=10)

    assert result.median_dollar_vol is None
    assert result.median_trade_count is None
    assert result.gap_ratio is None
    assert result.note != ""
    assert "CAND" in result.note or "bars" in result.note.lower()


def test_candidate_symbol_absent_from_bars_yields_liquidity_fields_none_with_a_stated_reason(
    bars_ohlcv,
):
    """Same D-38 contract as the `bars=None` case, but for the more common
    real situation: `bars` exists and covers other symbols, just not this
    candidate's (a newly listed or thinly covered ticker, say). `bars_ohlcv`
    only ever covers CAND, so GHOST is absent by construction.

    Falsifiable by: the same failure shapes as the `bars=None` test above --
    a raise, a 0.0, or a `None` with no stated reason.
    """
    candidate = _candidate(symbol="GHOST", as_of=bars_ohlcv["timestamp"].max().date())

    result = compute_day_trade_perspective(candidate, bars_ohlcv, trail=10)

    assert result.median_dollar_vol is None
    assert result.median_trade_count is None
    assert result.gap_ratio is None
    assert result.note != ""
    assert "GHOST" in result.note or "bars" in result.note.lower()


def test_not_measurable_always_lists_spread_slippage_and_borrow(bars_ohlcv):
    """TASK-2.4: `not_measurable` unconditionally names `spread`, `slippage`
    and `borrow`, each with its own reason -- including here, on the fully
    populated happy path where `bars_ohlcv` covers the candidate and every
    liquidity field comes back non-`None`. LagMatrix has no quote, fill or
    borrow-rate data anywhere (Q-33/Q-22); this field exists so that absence
    is stated in the payload rather than left for a reader to infer from
    three fields that simply never appear.

    Each entry is asserted to carry a `"kind"` and a non-empty `"reason"` --
    an explicit assumption about `list[dict[str, str]]`'s shape, since the
    plan does not name the dict's keys; flagged in the handoff rather than
    guessed silently past.

    Falsifiable by: making `not_measurable` conditional on missing data
    (e.g. populated only when `bars` is `None`/the symbol is absent, and
    empty here where `bars_ohlcv` fully covers CAND) -- this test would then
    fail on this happy-path call even though the TASK-2.3 tests still pass.
    """
    as_of = bars_ohlcv["timestamp"].max().date()
    candidate = _candidate(symbol="CAND", as_of=as_of)

    result = compute_day_trade_perspective(candidate, bars_ohlcv, trail=10)

    # Happy path: the liquidity read itself succeeded, so this test is
    # actually exercising the unconditional case, not TASK-2.3's fallback.
    assert result.median_dollar_vol is not None
    assert result.median_trade_count is not None

    kinds = {entry["kind"] for entry in result.not_measurable}
    assert {"spread", "slippage", "borrow"} <= kinds
    for entry in result.not_measurable:
        assert entry["reason"]
