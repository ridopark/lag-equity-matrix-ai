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
from langgraph.runtime import Runtime

from lagmatrix.adapters.llm import cap_for_llm
from lagmatrix.domain.models import Candidate, DayTradeAnalystNote, DayTradePerspective
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState, candidate_key

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


DAY_TRADE_SYSTEM_PROMPT = """\
You are the day-trade analyst for a stock-lag pipeline. You are given a
deterministic read of one candidate's own trailing liquidity and gap
behaviour: trailing median dollar volume, median trade count, the number of
trailing sessions, and a gap-vs-intraday-range ratio, describing measured
history only, never a forecast of tomorrow's session. You are also told,
unconditionally, which execution-cost dimensions this pipeline cannot
measure at all (spread, slippage, borrow) and why. Classify the candidate's
liquidity tier and whether gap moves dominate its history, and never treat
an unmeasurable dimension as if it were fine -- it is unknown, not zero.
Never report a calibrated probability -- only a status and a classification.
"""


def _brief(candidate: Candidate, dtp: DayTradePerspective) -> str:
    not_measurable_lines = "\n".join(
        f"not_measurable: {entry['kind']} -- {entry['reason']}" for entry in dtp.not_measurable
    )
    return (
        f"{candidate.symbol} as of {candidate.as_of.isoformat()} ({candidate.direction})\n"
        f"median_dollar_vol={dtp.median_dollar_vol}\n"
        f"median_trade_count={dtp.median_trade_count}\n"
        f"n_sessions={dtp.n_sessions}\n"
        f"gap_ratio={dtp.gap_ratio}\n"
        f"{not_measurable_lines}\n"
        f"note: {dtp.note}"
    )


async def analyse_day_trade(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    day_trade_by_key = state.get("day_trade_by_key", {})
    candidates = [
        c for c in state.get("candidates", []) if candidate_key(c) in day_trade_by_key
    ]
    llm = runtime.context.llm

    if llm is None:
        return {
            "day_trade_analyst_by_key": {
                candidate_key(c): DayTradeAnalystNote(
                    status="not_run", reasoning="no LLM configured"
                )
                for c in candidates
            }
        }

    max_n = runtime.context.max_llm_candidates
    if max_n is None:
        selected, capped = candidates, []
    else:
        selected, capped = cap_for_llm(candidates, max_n)

    result: dict[str, DayTradeAnalystNote] = {
        candidate_key(c): DayTradeAnalystNote(
            status="not_run", reasoning=f"batch cap reached ({max_n})"
        )
        for c in capped
    }

    briefs = {
        candidate_key(c): _brief(c, day_trade_by_key[candidate_key(c)]) for c in selected
    }
    if briefs:
        notes = await llm.classify(
            briefs, DayTradeAnalystNote, system_prompt=DAY_TRADE_SYSTEM_PROMPT
        )
        result.update(notes)

    return {"day_trade_analyst_by_key": result}
