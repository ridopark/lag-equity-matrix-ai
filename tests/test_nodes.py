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

from lagmatrix.domain.models import Candidate, LagEdge, Shock
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.assessor import assess
from lagmatrix.graph.nodes.context_fusion import fuse_evidence
from lagmatrix.graph.nodes.graph_retriever import retrieve_neighbourhood
from lagmatrix.graph.nodes.leader_state import leader_state
from lagmatrix.graph.state import candidate_key


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


def _fake_arango_edge(leader: str, lagger: str, lag_days: int = 1) -> LagEdge:
    """A canned edge shaped like `ArangoTopology.laggers_of`'s real output
    (D-79): `leader` is the candidate passed in, `lagger` is the supplier
    reached -- the opposite convention from the correlation edges above,
    where `leader` is the neighbour and `lagger` is the candidate.
    """
    return LagEdge(
        leader=leader, lagger=lagger, correlation=0.0, lag_days=lag_days,
        beta=0.42, relation="supplier",
    )


class FakeArangoTopology:
    """Fake `ArangoTopology`-shaped adapter, mirroring `FlakyNewsClient`'s
    fake-adapter pattern in `test_news_node.py`. Returns canned `LagEdge`s
    from `laggers_of` and records every call's arguments so tests can assert
    on them without touching a real ArangoDB.
    """

    def __init__(self, edges_by_leader: dict[str, list[LagEdge]]):
        self._edges_by_leader = edges_by_leader
        self.calls: list[tuple[str, int, date]] = []

    def laggers_of(self, leader: str, max_hops: int, as_of: date) -> list[LagEdge]:
        self.calls.append((leader, max_hops, as_of))
        return self._edges_by_leader.get(leader, [])


def test_graph_retriever_arango_edges_are_additive_not_conditional(closes):
    """PHASE-4/TASK-4.1 (D-79): when `arango_topology` is set, its edges must
    appear *alongside* the existing correlation edges for a candidate that
    produces both, not instead of them.

    Falsifies if: `lag_edges_by_key[key]` is missing any of the correlation
    edges (the ArangoDB path replaced them) or is missing the fake's edge
    (the ArangoDB path was never consulted) -- i.e. if the merge is
    conditional rather than additive.
    """
    cand = _candidate()
    key = candidate_key(cand)

    baseline_rt = _runtime(closes)
    baseline = retrieve_neighbourhood({"candidates": [cand]}, baseline_rt)["lag_edges_by_key"][key]
    assert baseline, "fixture must produce correlation edges for this test to mean anything"

    fake_edge = _fake_arango_edge(leader=cand.symbol, lagger="AVGO")
    fake = FakeArangoTopology({cand.symbol: [fake_edge]})
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), arango_topology=fake, max_lag_hops=2
        )
    )

    combined = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges_by_key"][key]

    assert len(combined) == len(baseline) + 1
    for edge in baseline:
        assert edge in combined
    assert fake_edge in combined


def test_graph_retriever_reads_max_lag_hops_from_context(closes):
    """PHASE-4/TASK-4.1 (D-79): the hop bound and traversal endpoint passed
    to `laggers_of` must come from `runtime.context`, not be hardcoded.

    Falsifies if: `max_lag_hops`'s long-standing default of 2 is hardcoded
    instead of read from context (caught by using 3 here, a non-default
    value), or if the candidate is passed on the wrong end -- `leader` must
    be the candidate's own symbol and `as_of` must be the candidate's own
    date, per D-79's "the candidate is the leader, not the lagger".
    """
    cand = _candidate(sym="CAND", d=date(2026, 6, 1))
    fake = FakeArangoTopology({})
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), arango_topology=fake, max_lag_hops=3
        )
    )

    retrieve_neighbourhood({"candidates": [cand]}, rt)

    assert fake.calls == [(cand.symbol, 3, cand.as_of)]


def test_graph_retriever_byte_identical_when_arango_disabled(closes):
    """PHASE-4/TASK-4.1 (D-79): with `arango_topology` left at its default
    (`None`), the output must be byte-identical to today's correlation-only
    behaviour -- this is the test that protects `scripts/check_baseline.py`.

    Falsifies if: adding the new code path perturbs any existing edge,
    weight, or ordering in `lag_edges`, `lag_edges_by_key`, or `errors` --
    a strict, total equality check, not a spot check on one field.
    """
    cand = _candidate()

    baseline_rt = _runtime(closes)
    baseline = retrieve_neighbourhood({"candidates": [cand]}, baseline_rt)

    explicit_none_rt = Runtime(
        context=LagMatrixContext(closes=closes, signal_universe=set(), arango_topology=None)
    )
    out = retrieve_neighbourhood({"candidates": [cand]}, explicit_none_rt)

    assert out == baseline


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


def _supply_edge(supplier: str) -> LagEdge:
    """A `laggers_of`-shaped supply edge (D-79): `leader` is the candidate
    itself, `lagger` is the supplier reached -- the opposite orientation from
    a correlation edge, where `leader` is the neighbour and `lagger` is the
    candidate. Same shape as `_fake_arango_edge` above, kept separate here so
    each test in this block states its own edge inline.
    """
    return LagEdge(
        leader="CAND", lagger=supplier, correlation=0.0, lag_days=1,
        beta=0.42, relation="supplier",
    )


def test_fuse_evidence_does_not_crash_when_correlation_and_supply_edges_coexist(closes):
    """PHASE-4 wired ArangoDB supply edges into `graph_retriever` additively
    (D-79), alongside the pre-existing correlation edges, so a candidate's
    `lag_edges_by_key` entry now mixes both orientations: correlation edges
    have `leader` = a neighbour, `lagger` = the candidate; supply edges have
    `leader` = the candidate, `lagger` = a supplier. `fuse_evidence` reads
    only `e.leader`, so every supply edge contributes the candidate's own
    symbol to `leaders` -- once per supplier.

    Three suppliers plus a correlating neighbour (LEAD1, both shocked, as
    real `leader_state` output would have -- it unconditionally attaches a
    self-shock for the candidate, see `leader_state.py`'s `sym == c.symbol`
    clause) makes `movers` repeat "CAND" three times. `sub.corr()` then has
    a duplicate "CAND" column label, so `rho["CAND"]` returns a DataFrame
    instead of a Series and `int(...)` blows up.

    Falsifies if: this raises `TypeError: int() argument must be a string,
    a bytes-like object or a real number, not 'Series'` -- the exact crash
    from mixing edge orientations without accounting for direction.
    """
    cand = _candidate(sym="CAND", d=date(2026, 6, 1))
    key = candidate_key(cand)
    corr_edge = LagEdge(
        leader="LEAD1", lagger="CAND", correlation=0.5, lag_days=0,
        beta=0.3, relation="correlation",
    )
    supply_edges = [_supply_edge("SUPPLIER1"), _supply_edge("SUPPLIER2"), _supply_edge("SUPPLIER3")]

    state = {
        "candidates": [cand],
        "lag_edges_by_key": {key: [corr_edge, *supply_edges]},
        "leader_shocks": {
            key: [
                Shock(symbol="LEAD1", pct_change=0.05, sigma=3.0,
                      lookback_days=60, date=cand.as_of),
                Shock(symbol="CAND", pct_change=0.0005, sigma=0.1,
                      lookback_days=60, date=cand.as_of),
            ]
        },
    }
    rt = _runtime(closes)

    out = fuse_evidence(state, rt)  # must not raise

    assert out["evidence"]


def test_supply_edges_contribute_no_leader_move_evidence(closes):
    """D-73: a supply edge's `leader` is the candidate itself (D-79's
    orientation), so the candidate is never the *lagger* of its own supplier
    -- a supplier's move can never be `leader_move` evidence *for* the
    candidate; that inference runs the other way (customer -> supplier, not
    supplier -> customer).

    The self-shock is included because `leader_state` unconditionally
    attaches one for the candidate (`sym == c.symbol`, see `leader_state.py`)
    -- without it this test would pass by accident, since today's code reads
    only `e.leader` and a supply-only edge list never puts the supplier's own
    symbol into `leaders` at all. With the self-shock present, today's code
    instead manufactures a spurious self-referential `leader_move` entry
    (symbol="CAND") once per supply edge and inflates `effective_evidence`,
    which is what this test catches.

    Falsifies if: `effective_evidence` is nonzero, or any `Evidence` names
    the supplier as a leader move -- either would mean a supply edge (where
    the candidate is the leader, not the lagger) got treated as evidence
    about the candidate.
    """
    cand = _candidate(sym="CAND", d=date(2026, 6, 1))
    key = candidate_key(cand)
    state = {
        "candidates": [cand],
        "lag_edges_by_key": {key: [_supply_edge("SUPPLIER1")]},
        "leader_shocks": {
            key: [
                Shock(symbol="SUPPLIER1", pct_change=0.05, sigma=3.0,
                      lookback_days=60, date=cand.as_of),
                Shock(symbol="CAND", pct_change=0.0005, sigma=0.1,
                      lookback_days=60, date=cand.as_of),
            ]
        },
    }
    rt = _runtime(closes)

    out = fuse_evidence(state, rt)

    assert "SUPPLIER1" not in {e.symbol for e in out["evidence"]}
    assert out["effective_evidence"] == 0.0


def test_correlation_only_evidence_is_unchanged_by_the_supply_edge_fix():
    """Regression pin for `scripts/check_baseline.py`: a candidate whose
    `lag_edges` are all correlation edges (no supply edges at all -- the
    shape every candidate had before PHASE-4/D-79) must keep producing
    exactly today's evidence, weights and `effective_evidence`. Captured
    from a real `retrieve_neighbourhood` -> `leader_state` -> `fuse_evidence`
    run over `_shocked_closes()` (also used by
    `test_fuse_evidence_effective_evidence_never_exceeds_raw_count`).

    Falsifies if: a fix for the supply-edge orientation bug changes any
    correlation-only result -- symbol, supports, weight or the effective
    count -- not just a spot check on one field.
    """
    closes = _shocked_closes()
    cand = _candidate(d=date(2026, 3, 26))
    rt = _runtime(closes)
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]

    out = fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)

    assert {(e.kind, e.symbol, e.supports, e.weight) for e in out["evidence"]} == {
        ("leader_move", "LEAD1", True, 0.5),
        ("leader_move", "LEAD2", True, 0.5),
    }
    assert out["effective_evidence"] == 1.0


# --- Q-38: leader_state's baseline must not overlap the recent window -----
#
# `leader_state.py` currently computes `baseline = returns.iloc[ti-trail:ti]`
# and `recent = returns.iloc[ti-move_win:ti]` -- the latter is literally the
# tail of the former, so the move being measured sits inside the sample its
# own sigma is estimated from. `shocks.standardised_moves` documents the
# opposite requirement ("baseline ... must end strictly before returns
# begins") and `MarketScan.shocked_leaders` (adapters/candidates.py) already
# honours it with adjacent, non-overlapping windows
# (`returns.iloc[ti-move_win-trail:ti-move_win]`). `leader_state` must match.

TRAIL = 60
MOVE_WIN = 3
SIGMA = 2.0




