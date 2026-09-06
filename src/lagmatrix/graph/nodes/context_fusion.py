"""Assemble neighbourhood observations into independence-weighted evidence.

Weighting, not counting (Q-12). Correlated neighbours are one observation seen
several times, so a raw count of corroborating names overstates the evidence —
and it overstates it most when the graph is working best, because the graph
selects for correlation. Weight is 1/(cluster size) at a correlation threshold,
so twenty names moving as one bloc contribute about one unit, not twenty.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from lagmatrix.domain.models import Evidence
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState, candidate_key, edges_for


def fuse_evidence(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    closes = runtime.context.closes
    trail = runtime.context.trail
    cluster_rho = runtime.context.cluster_rho
    returns = closes.pct_change()
    sessions = closes.index

    leader_shocks_by_key = state.get("leader_shocks", {})
    news_by_key = state.get("news", {})

    evidence: list[Evidence] = []
    effective = 0.0
    evidence_by_key: dict[str, list[Evidence]] = {}
    effective_by_key: dict[str, float] = {}

    for c in state.get("candidates", []):
        key = candidate_key(c)
        leaders = [e.leader for e in edges_for(state, c)]
        if not leaders:
            continue

        shocks = {s.symbol: s for s in leader_shocks_by_key.get(key, [])}
        movers = [s for s in leaders if s in shocks]

        c_evidence: list[Evidence] = []
        c_effective = 0.0

        if movers:
            ti = sessions.get_loc(sessions[sessions > str(c.as_of)][0])
            win = returns.iloc[ti - trail : ti]
            sub = win[[m for m in movers if m in win.columns]]
            rho = sub.corr().abs() if sub.shape[1] > 1 else None

            cand_z = shocks[c.symbol].sigma if c.symbol in shocks else 0.0
            want = 1.0 if c.direction == "up" else -1.0

            for m in movers:
                z = shocks[m].sigma
                # cluster size: how many other movers this one moves with
                bloc = 1 if rho is None else int((rho[m] >= cluster_rho).sum())
                w = 1.0 / max(bloc, 1)
                supports = (z * want > 0) and abs(cand_z) < abs(z)
                c_effective += w
                c_evidence.append(
                    Evidence(
                        kind="leader_move",
                        symbol=m,
                        supports=supports,
                        weight=round(w, 4),
                        detail=(f"{m} moved {z:+.2f}σ while {c.symbol} moved "
                                f"{cand_z:+.2f}σ; bloc of {bloc}"),
                    )
                )

        news_n = len(news_by_key.get(key, []))
        if news_n:
            c_evidence.append(
                Evidence(kind="co_mention", symbol=c.symbol, supports=True,
                         weight=0.0,  # context only — not counted as evidence
                         detail=f"{news_n} articles in the {c.as_of} lookback")
            )

        evidence_by_key[key] = c_evidence
        effective_by_key[key] = round(c_effective, 3)
        evidence.extend(c_evidence)
        effective += c_effective

    return {
        "evidence": evidence,
        "effective_evidence": round(effective, 3),
        "evidence_by_key": evidence_by_key,
        "effective_evidence_by_key": effective_by_key,
    }
