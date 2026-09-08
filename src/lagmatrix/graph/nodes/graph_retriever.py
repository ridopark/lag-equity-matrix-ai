"""Structured half of GraphRAG: each candidate's neighbourhood, as of its date.

Point-in-time by construction — the correlation window ends the session *before*
the candidate's date, so a neighbourhood never reflects prices the trader had not
seen (D-16). Neighbours are drawn from the wide universe and never from the
signal's own tickers (D-27).
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from lagmatrix.domain.models import LagEdge
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState, candidate_key


def retrieve_neighbourhood(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    closes = runtime.context.closes
    signal_universe = runtime.context.signal_universe
    excluded_symbols = runtime.context.excluded_symbols
    trail = runtime.context.trail
    topk = runtime.context.topk
    returns = closes.pct_change()
    sessions = closes.index

    candidates = [state["candidate"]] if "candidate" in state else state.get("candidates", [])

    edges: list[LagEdge] = []
    lag_edges_by_key: dict[str, list[LagEdge]] = {}
    errors: list[str] = []
    for c in candidates:
        key = candidate_key(c)
        c_edges: list[LagEdge] = []
        later = sessions[sessions > str(c.as_of)]
        if len(later) == 0 or c.symbol not in closes.columns:
            errors.append(f"{c.symbol} {c.as_of}: no session or no bars")
            lag_edges_by_key[key] = c_edges
            continue
        ti = sessions.get_loc(later[0])
        if ti < trail:
            errors.append(f"{c.symbol} {c.as_of}: insufficient history")
            lag_edges_by_key[key] = c_edges
            continue

        win = returns.iloc[ti - trail : ti]
        cand = win[c.symbol]
        # Q-26: the candidate always correlates with itself at rho=1.0, so its own
        # column must be dropped unconditionally — not only when it happens to be
        # in `signal_universe` — or it wins its own top-k as a guaranteed self-edge.
        drop_cols = [s for s in signal_universe | excluded_symbols | {c.symbol} if s in win.columns]
        pool = win.drop(columns=drop_cols)
        pool = pool.loc[:, pool.notna().sum() >= trail * 0.8]
        corr = pool.corrwith(cand).dropna()
        for leader in corr.abs().nlargest(topk).index:
            c_edges.append(
                LagEdge(
                    leader=str(leader),
                    lagger=c.symbol,
                    correlation=float(corr[leader]),
                    lag_days=0,  # contemporaneous correlation; lag estimation is future work
                    beta=float(corr[leader] * win[leader].std() / cand.std()),
                    relation="correlation",
                )
            )
        if runtime.context.arango_topology is not None:
            c_edges.extend(
                runtime.context.arango_topology.laggers_of(
                    c.symbol, runtime.context.max_lag_hops, c.as_of
                )
            )
        lag_edges_by_key[key] = c_edges
        edges.extend(c_edges)

    out: dict = {"lag_edges": edges, "lag_edges_by_key": lag_edges_by_key, "errors": errors}
    if "candidate" in state:
        out["candidate"] = state["candidate"]
    return out
