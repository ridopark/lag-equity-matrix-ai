"""PHASE-1: nodes are plain module-level functions taking (state, runtime).

Each test imports a node directly and calls it with a hand-built `Runtime` —
no graph is constructed (REQ-2). This also pins D-27 at the node level, one
layer below the graph-level check in `test_graph_builder.py`.
"""

from __future__ import annotations

from datetime import date

from langgraph.runtime import Runtime

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.context_fusion import fuse_evidence
from lagmatrix.graph.nodes.graph_retriever import retrieve_neighbourhood
from lagmatrix.graph.nodes.leader_state import leader_state


def _candidate(sym="CAND", d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def _runtime(closes, signal_universe=frozenset()) -> Runtime[LagMatrixContext]:
    return Runtime(context=LagMatrixContext(closes=closes, signal_universe=set(signal_universe)))


def test_retrieve_neighbourhood_is_callable_without_a_graph(closes):
    rt = _runtime(closes)
    out = retrieve_neighbourhood({"candidates": [_candidate()]}, rt)
    assert out["lag_edges"]


def test_retrieve_neighbourhood_excludes_the_signal_universe(closes):
    """D-27 at node level: neighbours never come from the signal's own tickers."""
    rt = _runtime(closes, signal_universe={"LEAD1", "LEAD2"})
    out = retrieve_neighbourhood({"candidates": [_candidate()]}, rt)
    assert {e.leader for e in out["lag_edges"]}.isdisjoint({"LEAD1", "LEAD2"})


def test_retrieve_neighbourhood_excludes_leveraged_products(closes):
    """A leveraged/inverse ETF built on the candidate's own underlying (e.g.
    AAPD, a -1x AAPL product) correlates near-perfectly with the candidate by
    construction and carries no independent information — it must never
    surface as a leader. Same shape as D-27's signal-universe exclusion
    (test_retrieve_neighbourhood_excludes_the_signal_universe), but sourced
    from `excluded_symbols` instead of `signal_universe`. CANDD is a synthetic
    -1x inverse of CAND, standing in for AAPD relative to AAPL.
    """
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), excluded_symbols={"CANDD"}
        )
    )
    out = retrieve_neighbourhood({"candidates": [_candidate()]}, rt)
    assert {e.leader for e in out["lag_edges"]}.isdisjoint({"CANDD"})


def test_excluding_leveraged_products_does_not_empty_the_neighbourhood(closes):
    """Guards the obvious over-correction: excluding one leveraged product
    must not wipe out the whole neighbourhood — CAND's real leaders
    (LEAD1/LEAD2) still surface as evidence.
    """
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), excluded_symbols={"CANDD"}
        )
    )
    out = retrieve_neighbourhood({"candidates": [_candidate()]}, rt)
    assert out["lag_edges"]


def test_leader_state_is_callable_without_a_graph(closes):
    rt = _runtime(closes)
    cand = _candidate()
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    out = leader_state({"candidates": [cand], "lag_edges": edges}, rt)
    assert out["leader_shocks"]


def test_fuse_evidence_effective_evidence_never_exceeds_raw_count(closes):
    """Q-12 at node level: the independence weight can only discount, not inflate."""
    rt = _runtime(closes)
    cand = _candidate()
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]
    out = fuse_evidence(
        {"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt
    )
    assert out["evidence"]
    assert out["effective_evidence"] <= len(out["evidence"]) + 1e-9
