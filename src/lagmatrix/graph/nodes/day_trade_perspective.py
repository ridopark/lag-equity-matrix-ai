"""Deterministic day-trade read of a candidate's own trailing liquidity and
gap behaviour (PHASE-2).

Trailing liquidity (`median_dollar_vol`/`median_trade_count`) and the
gap/intraday split (`gap_ratio`) are read from `data/bars.parquet`-shaped
daily bars, over sessions strictly before `candidate.as_of` (D-16) --
describes measured history only, never a forecast of tomorrow's session
(D-96). `not_measurable` states, unconditionally, what this pipeline has no
data for at all: spread, slippage and borrow cost (Q-33/Q-22).
"""

from __future__ import annotations

import pandas as pd

from lagmatrix.domain.models import Candidate, DayTradePerspective

_NOT_MEASURABLE = [
    {"kind": "spread", "reason": "no bid/ask quote data in this pipeline"},
    {"kind": "slippage", "reason": "no fill/execution data in this pipeline"},
    {"kind": "borrow", "reason": "no borrow-rate data in this pipeline"},
]


def compute_day_trade_perspective(
    candidate: Candidate, bars: pd.DataFrame | None, trail: int
) -> DayTradePerspective:
    if bars is None or candidate.symbol not in set(bars["symbol"]):
        return DayTradePerspective(
            median_dollar_vol=None,
            median_trade_count=None,
            n_sessions=0,
            gap_ratio=None,
            not_measurable=list(_NOT_MEASURABLE),
            note=f"no bars data for {candidate.symbol}",
        )

    symbol_bars = bars[bars["symbol"] == candidate.symbol].sort_values("timestamp")
    prior_close = symbol_bars["close"].shift(1)
    as_of_ts = pd.Timestamp(candidate.as_of, tz=symbol_bars["timestamp"].dt.tz)
    # D-16: strictly before as_of -- never the as_of session's own bar.
    trailing = symbol_bars[symbol_bars["timestamp"] < as_of_ts].tail(trail)
    n_sessions = len(trailing)

    if n_sessions == 0:
        median_dollar_vol = None
        median_trade_count = None
        gap_ratio = None
    else:
        median_dollar_vol = float(trailing["dollar_vol"].median())
        median_trade_count = float(trailing["trade_count"].median())

        # Gap: |open - prior session's close|. Intraday range: |high - low|.
        # Ratio isolates *where* the historical move happened, not how much.
        gap = (trailing["open"] - prior_close.loc[trailing.index]).abs()
        intraday_range = (trailing["high"] - trailing["low"]).abs()
        denom = gap.sum() + intraday_range.sum()
        gap_ratio = float(gap.sum() / denom) if denom else None

    return DayTradePerspective(
        median_dollar_vol=median_dollar_vol,
        median_trade_count=median_trade_count,
        n_sessions=n_sessions,
        gap_ratio=gap_ratio,
        not_measurable=list(_NOT_MEASURABLE),
        note=f"{n_sessions} trailing session(s) over a {trail}-session window",
    )
