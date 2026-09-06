"""PHASE-1: nodes are plain module-level functions taking (state, runtime).

Each test imports a node directly and calls it with a hand-built `Runtime` —
no graph is constructed (REQ-2). This also pins D-27 at the node level, one
layer below the graph-level check in `test_graph_builder.py`.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from langgraph.runtime import Runtime

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.assessor import assess
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


def test_retrieve_neighbourhood_never_includes_the_candidate_as_its_own_leader(closes):
    """Q-26: the candidate's own column must never surface as its own leader.

    Unlike `test_retrieve_neighbourhood_excludes_the_signal_universe`, CAND is
    deliberately *not* in `signal_universe` here — the D-27 exclusion happens
    to catch every candidate today only because every alert ticker is also in
    `signal_universe`. Under a `MarketScan`-style candidate (D-23), a candidate
    is not guaranteed to be in `signal_universe`, so this must hold on its own:
    with an empty `signal_universe`, CAND still correlates with itself at
    rho=1.0 and must not be returned as a leader of itself.
    """
    rt = _runtime(closes, signal_universe=frozenset())
    out = retrieve_neighbourhood({"candidates": [_candidate()]}, rt)
    assert "CAND" not in {e.leader for e in out["lag_edges"]}


def test_retrieve_neighbourhood_excludes_signal_universe_and_leveraged_products_together(closes):
    """Guards against a self-edge fix that swaps one exclusion for another:
    the pre-existing `signal_universe` and `excluded_symbols` exclusions must
    keep working when both are populated at once."""
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe={"LEAD1", "LEAD2"}, excluded_symbols={"CANDD"}
        )
    )
    out = retrieve_neighbourhood({"candidates": [_candidate()]}, rt)
    leaders = {e.leader for e in out["lag_edges"]}
    assert leaders.isdisjoint({"LEAD1", "LEAD2", "CANDD"})


def _self_edge_closes() -> pd.DataFrame:
    """A hand-built universe where CAND's two real neighbours (LEAD1, LEAD2)
    both shock upward in the last `move_win` sessions while CAND itself barely
    moves — a textbook corroborating setup, with no genuine reason for CAND to
    ever contradict itself.

    12 sessions: session 0 is a throwaway base row (`pct_change` of the first
    row is always NaN), sessions 1-10 are the `trail=10` correlation/baseline
    window, and session 10 (the last of that window) is the candidate's
    `as_of` date — session 11 exists only so a session "after" `as_of` is
    available (D-16 point-in-time check). Sessions 1-7 are quiet, sessions
    8-10 carry the shock.
    """
    quiet = [0.001, -0.002, 0.0015, -0.001, 0.0005, -0.0015, 0.001]
    shock = [0.05, 0.06, 0.04]
    lead1 = quiet + shock
    lead2 = [x * 0.9 for x in quiet] + [x * 0.95 for x in shock]
    cand = [x * 0.8 for x in quiet] + [0.0005, 0.0004, 0.0003]

    idx = pd.bdate_range("2026-01-01", periods=12, tz="UTC")
    r = {"LEAD1": [0.0, *lead1, 0.0], "LEAD2": [0.0, *lead2, 0.0], "CAND": [0.0, *cand, 0.0]}
    return pd.DataFrame({k: 100 * np.cumprod([1 + x for x in v]) for k, v in r.items()}, index=idx)


def test_self_edge_never_becomes_a_contradicting_evidence_unit():
    """Q-26 end to end: chases the self-edge bug through `leader_state` and
    `fuse_evidence` into `assess`.

    CAND is not in `signal_universe` (the `MarketScan`-like condition — see
    `test_retrieve_neighbourhood_never_includes_the_candidate_as_its_own_leader`),
    so the self-edge bug is live here. LEAD1 and LEAD2 are CAND's only real
    neighbours and both genuinely corroborate (they shock upward while CAND's
    thesis is "up"), so a correct pipeline has nothing to contradict CAND with.

    Currently, `leader_state`'s `sym == c.symbol` clause always manufactures a
    shock for CAND itself, and because the self-edge from `graph_retriever`
    puts CAND in its own leader list, `fuse_evidence`'s `abs(cand_z) < abs(z)`
    check is comparing CAND's sigma to itself and is always False — one
    guaranteed contradicting unit, dragging a would-be "corroborated" verdict
    down to "neutral".
    """
    closes = _self_edge_closes()
    cand = Candidate(symbol="CAND", direction="up", as_of=date(2026, 1, 15), origin="external")
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), trail=10, topk=2, move_win=3, sigma=2.0
        )
    )

    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]
    fused = fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)
    assessed = assess(
        {
            "candidates": [cand],
            "evidence_by_key": fused["evidence_by_key"],
            "effective_evidence_by_key": fused["effective_evidence_by_key"],
        }
    )
    assessment = assessed["assessments"][0]

    assert "CAND" not in {e.symbol for e in fused["evidence"]}
    assert assessment.contradicting == []
    assert assessment.verdict == "corroborated"


def test_leader_state_is_callable_without_a_graph(closes):
    rt = _runtime(closes)
    cand = _candidate()
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    out = leader_state({"candidates": [cand], "lag_edges": edges}, rt)
    assert out["leader_shocks"]


def _shocked_closes() -> pd.DataFrame:
    """A hand-built universe where CAND's two real neighbours (LEAD1, LEAD2)
    shock upward in the last `move_win=3` sessions of the trailing `trail=60`
    baseline, while CAND itself stays quiet.

    The shared `closes` fixture has no real neighbour cross sigma=2.0 at
    CAND's as_of (2026-06-01) -- LEAD1/LEAD2/LEAD3/INDEP all sit well under
    threshold there -- so `fuse_evidence` sees only the Q-26 self-edge case
    exercises, not this one. This fixture gives CAND a genuine corroborating
    neighbourhood instead.

    62 sessions: session 0 is a throwaway base row (`pct_change` of the first
    row is always NaN), sessions 1-60 are the `trail=60` baseline/correlation
    window (57 quiet sessions then a 3-session shock in LEAD1/LEAD2 -- session
    60 is the candidate's `as_of`), and session 61 exists only so a session
    "after" `as_of` is available (D-16 point-in-time check).
    """
    rng = np.random.default_rng(0)
    n_quiet = 57
    common = rng.normal(0, 0.01, n_quiet)
    lead1_q = common + rng.normal(0, 0.001, n_quiet)
    lead2_q = common + rng.normal(0, 0.001, n_quiet)
    cand_q = common * 0.8 + rng.normal(0, 0.004, n_quiet)
    shock = [0.05, 0.06, 0.04]
    lead1 = list(lead1_q) + shock
    lead2 = list(lead2_q) + [x * 0.95 for x in shock]
    cand = list(cand_q) + [0.0005, 0.0004, 0.0003]

    idx = pd.bdate_range("2026-01-01", periods=62, tz="UTC")
    r = {"LEAD1": [0.0, *lead1, 0.0], "LEAD2": [0.0, *lead2, 0.0], "CAND": [0.0, *cand, 0.0]}
    return pd.DataFrame({k: 100 * np.cumprod([1 + x for x in v]) for k, v in r.items()}, index=idx)


def test_fuse_evidence_effective_evidence_never_exceeds_raw_count():
    """Q-12 at node level: the independence weight can only discount, not inflate.

    Also pins Q-26 at this seam: `_shocked_closes` leaves CAND out of
    `signal_universe`, so if the candidate's own column ever won its own
    top-k again (the self-edge regressing), it would appear in `evidence`
    alongside LEAD1/LEAD2 -- caught below even though the effective-count
    inequality alone wouldn't (a self-edge's weight is still <= 1).
    """
    rt = _runtime(_shocked_closes())
    cand = _candidate(d=date(2026, 3, 26))
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]
    out = fuse_evidence(
        {"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt
    )
    assert out["evidence"]
    assert out["effective_evidence"] <= len(out["evidence"]) + 1e-9
    assert cand.symbol not in {e.symbol for e in out["evidence"]}
