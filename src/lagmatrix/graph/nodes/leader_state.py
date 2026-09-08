"""Has the candidate's neighbourhood already moved, and has the candidate not?

The structural evidence. Uses `shocks.standardised_moves` so the same maths is
available to a future market scanner (D-23).
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from lagmatrix.domain.models import Shock
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState, candidate_key, edges_for
from lagmatrix.shocks import standardised_moves


def leader_state(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    closes = runtime.context.closes
    trail = runtime.context.trail
    move_win = runtime.context.move_win
    sigma = runtime.context.sigma
    returns = closes.pct_change()
    sessions = closes.index

    candidates = [state["candidate"]] if "candidate" in state else state.get("candidates", [])

    leader_shocks: dict[str, list[Shock]] = {}
    for c in candidates:
        leaders = [e.leader for e in edges_for(state, c)]
        if not leaders:
            continue
        ti = sessions.get_loc(sessions[sessions > str(c.as_of)][0])
        baseline = returns.iloc[ti - trail : ti]
        recent = returns.iloc[ti - move_win : ti]
        wanted = [*leaders, c.symbol]
        if c.origin_leader and c.origin_leader not in wanted:
            wanted.append(c.origin_leader)
        syms = [s for s in wanted if s in returns.columns]
        moves = standardised_moves(recent, syms, move_win, baseline)
        shocks: list[Shock] = []
        for sym, z in moves.items():
            if abs(z) >= sigma or sym == c.symbol or sym == c.origin_leader:
                shocks.append(
                    Shock(
                        symbol=str(sym),
                        pct_change=float(recent[sym].sum()),
                        sigma=float(z),
                        lookback_days=trail,
                        date=c.as_of,
                    )
                )
        leader_shocks[candidate_key(c)] = shocks

    out: dict = {"leader_shocks": leader_shocks}
    if "candidate" in state:
        out["candidate"] = state["candidate"]
    return out
