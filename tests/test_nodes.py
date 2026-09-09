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


# --- D-87: leader_state no longer special-cases origin_leader -------------


def _origin_leader_closes() -> pd.DataFrame:
    """A hand-built universe isolating the (now-reverted) `origin_leader`
    special-case from the ordinary correlation-derived `leaders` list.

    Three columns: OTHERLEAD (X's only `lag_edges` leader, wired directly in
    the test rather than via `retrieve_neighbourhood`), X (the candidate),
    and Y (the candidate's `origin_leader` -- deliberately *not* one of X's
    `lag_edges`, so a `Y` shock reaching `leader_shocks` would only be
    possible through special-casing `c.origin_leader`, never through the
    ordinary `leaders` list).

    trail=10, move_win=3. 12 sessions: session 0 is a throwaway base row
    (`pct_change` of the first row is always NaN), sessions 1-10 are the
    trail=10 baseline window with `as_of` landing on session 10 (the last of
    that window, so `recent` is the overlapping tail sessions 8-10 -- the
    pre-existing Q-38 baseline/recent overlap, unchanged here), and session
    11 exists only so a session strictly after `as_of` is available (D-16
    point-in-time lookup). Same total-session shape as `_self_edge_closes`
    above, which already exercises this exact slicing without an empty
    baseline/recent slice; confirmed non-empty here too by computing
    `shocks.standardised_moves` directly against this data before writing
    the assertions below (ti=11, baseline 10 rows, recent 3 rows), rather
    than assuming it.
    """
    y = [0.001, -0.001, 0.0015, -0.0005, 0.001, -0.0015, 0.0005, -0.001, 0.0012, -0.0008]
    x = [0.0009, -0.0011, 0.0013, -0.0006, 0.0011, -0.0016, 0.0004, -0.0009, 0.0015, -0.0007]
    other = [0.002, -0.0018, 0.0021, -0.0022, 0.0019, -0.002, 0.0022, -0.0021, 0.0023, -0.0019]
    idx = pd.bdate_range("2026-01-01", periods=12, tz="UTC")
    r = {"OTHERLEAD": [0.0, *other, 0.0], "X": [0.0, *x, 0.0], "Y": [0.0, *y, 0.0]}
    return pd.DataFrame(
        {k: 100 * np.cumprod([1 + v for v in vals]) for k, vals in r.items()}, index=idx
    )


def test_leader_state_ignores_origin_leader_now_that_lag_response_is_removed():
    """D-87/PHASE-1: `leader_state` reverts the D-84-era special-casing of
    `c.origin_leader` (`wanted.append(c.origin_leader)` and `sym ==
    c.origin_leader` in the sigma filter) -- with `lag_response` deleted,
    nothing downstream reads the origin leader's own shock any more; the
    `description` mechanism (D-87) only needs the candidate's own z and the
    leader's name, both already available without this special-case.

    Reuses `_origin_leader_closes()` verbatim: Y is engineered to sit
    genuinely below sigma=2.0 (`|z| = 0.31`, computed directly against this
    fixture before writing this assertion) and is deliberately absent from
    `lag_edges` (only OTHERLEAD->X is wired), so the *only* way a `Y` shock
    could appear in `leader_shocks` is via the reverted special-case.

    Uses the fixture's own `trail=10`/`move_win=3` context, already proven
    non-empty (baseline 10 rows, recent 3 rows) -- avoids the short-history
    trap where too few sessions yields an empty baseline slice and every
    downstream assertion silently tests nothing.

    Falsifies if: a `Shock` with `symbol == "Y"` still appears in
    `leader_shocks` for this candidate -- i.e. `leader_state` still
    force-resolves `c.origin_leader`. Fails today: the reversion has not
    happened yet, so Y is still force-resolved below sigma.
    """
    closes = _origin_leader_closes()
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    edges = [
        LagEdge(
            leader="OTHERLEAD", lagger="X", correlation=0.5, lag_days=0,
            beta=0.3, relation="correlation",
        )
    ]
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), trail=10, topk=2, move_win=3, sigma=2.0
        )
    )

    out = leader_state({"candidates": [cand], "lag_edges": edges}, rt)

    key = candidate_key(cand)
    assert not any(s.symbol == "Y" for s in out["leader_shocks"][key])


# --- D-87: fuse_evidence replaces room/origin_status with description -----


def test_fuse_evidence_admits_candidate_with_origin_leader_and_no_correlation_leaders(closes):
    """The one design decision (PLAN-2026-09-09-remove-room.md): the new
    admit gate is `if not leaders and not c.origin_leader: continue` --
    `origin_leader` being *set* is sufficient, whether it resolves to a
    `Shock` no longer matters (D-85's literal condition is gone, since
    nothing downstream reads the leader's own shock any more).

    `CAND` has `origin_leader="LEADUP"` and a `lag_edges_by_key` entry
    containing only `_supply_edge("SUPPLIER1")` (`leader=CAND`,
    `lagger="SUPPLIER1"`), so the `lagger == c.symbol` filter yields
    `leaders == []` -- zero correlation-filtered neighbours. `leader_shocks`
    is hand-built with a `Shock` for `CAND` itself but *none* for
    `LEADUP` -- deliberately: `LEADUP` does not resolve to a shock, which is
    exactly the fixture the plan's own trap warning calls out, because
    today's still-live gate (`not (c.origin_leader and c.origin_leader in
    shocks)`) also drops this candidate (`"LEADUP" not in shocks`, so its
    second clause is also true and the whole `if` fires). Verified directly
    against the unmodified source before writing this assertion: it fails
    today with `key not in out["effective_evidence_by_key"]` -- a real
    failure from the candidate being dropped, not a vacuous pass.

    Falsifies if: `key` is absent from `effective_evidence_by_key` (the
    candidate silently dropped -- option (b) from the design decision).
    """
    cand = Candidate(
        symbol="CAND", direction="up", as_of=date(2026, 6, 1), origin="scan",
        origin_leader="LEADUP",
    )
    key = candidate_key(cand)
    leader_shocks = {
        key: [Shock(symbol="CAND", pct_change=0.001, sigma=0.1, lookback_days=60, date=cand.as_of)]
    }
    state = {
        "candidates": [cand],
        "lag_edges_by_key": {key: [_supply_edge("SUPPLIER1")]},
        "leader_shocks": leader_shocks,
    }
    rt = _runtime(closes)

    out = fuse_evidence(state, rt)

    assert key in out["effective_evidence_by_key"]
    assert out["effective_evidence_by_key"][key] == 0.0
    assert out["evidence_by_key"][key] == []


def _origin_move_closes(x_recent: list[float]) -> pd.DataFrame:
    """Two columns, Y (the candidate X's `origin_leader`) and X itself --
    the minimal shape that isolates the `description` mechanism from any
    other neighbour, mirroring `_origin_leader_closes` above and
    `test_market_scan.py`'s `_reentry_fixture` construction.

    `trail=10`, `move_win=3`, `sigma=2.0`. 12 sessions: session 0 is a
    throwaway base row (`pct_change` of the first row is always NaN),
    sessions 1-10 are the `trail=10` baseline window with `as_of` landing
    on session 10 (so `recent` is the overlapping tail sessions 8-10 --
    the pre-existing Q-38 baseline/recent overlap, unchanged here, same as
    `_origin_leader_closes`), and session 11 is the D-16 point-in-time
    buffer.

    Y is quiet for 7 sessions then jumps +0.05/+0.06/+0.04 over `move_win`
    -- a large, unambiguous shock in the "up" direction. X is quiet for the
    same 7 sessions on its own loosely-correlated values (enough that
    `retrieve_neighbourhood`'s correlation always picks Y as X's one
    neighbour -- confirmed directly below, `leaders == ["Y"]` in every
    caller of this fixture); `x_recent` is the 3 values under each test's
    control. Every z value used below was computed by running the real
    `retrieve_neighbourhood` -> `leader_state` chain against this fixture
    before the assertions were written, not assumed.
    """
    quiet_y = [0.001, -0.001, 0.0015, -0.0005, 0.001, -0.0015, 0.0005]
    quiet_x = [0.0009, -0.0011, 0.0013, -0.0006, 0.0011, -0.0016, 0.0004]
    y = quiet_y + [0.05, 0.06, 0.04]
    x = quiet_x + list(x_recent)
    idx = pd.bdate_range("2026-01-01", periods=12, tz="UTC")
    r = {"Y": [0.0, *y, 0.0], "X": [0.0, *x, 0.0]}
    return pd.DataFrame(
        {k: 100 * np.cumprod([1 + v for v in vals]) for k, vals in r.items()}, index=idx
    )


def _run_origin_move_chain(closes: pd.DataFrame, cand: Candidate) -> dict:
    """`retrieve_neighbourhood` -> `leader_state` -> `fuse_evidence`, the
    real chain every `description` test below runs, over
    `_origin_move_closes`'s `trail=10`/`move_win=3`/`sigma=2.0` context."""
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), trail=10, topk=20, move_win=3, sigma=2.0
        )
    )
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]
    return fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)


def test_description_names_the_origin_leader_and_signed_move_toward_the_thesis():
    """D-87's replacement mechanism: `description_by_key[key]` is a plain
    sentence naming the candidate's own thesis-signed move (in its own
    sigma units) and the leader that surfaced it -- no threshold, no
    bucket, no denominator, no ordering.

    Reuses the deleted PHASE-3 block's "open, unmoved" fixture
    (`_origin_move_closes([0.0, 0.0, 0.0])`): X is quiet (`x_signed ==
    cand_z * want == 0.0`, computed directly against this fixture before
    writing this assertion -- same z-values the deleted
    `test_lag_response_open_when_candidate_has_not_moved` used), Y shocks
    +3.526436σ toward the "up" thesis. `x_signed = 0.0 >= 0`, so the
    description must read "toward", the signed value formatted `+0.00`.

    Falsifies if: `key` is absent from `description_by_key` (feature not
    implemented, or the admit gate/D-86 guard drops this candidate), or the
    string is missing the candidate symbol, the signed value, the direction
    word, or the leader's symbol -- any of which would mean the sentence is
    missing the fact it exists to state. Also pins that no `lag_response`
    `Evidence` is ever emitted (that unit no longer exists, Success
    Criterion 3).
    """
    closes = _origin_move_closes([0.0, 0.0, 0.0])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)

    out = _run_origin_move_chain(closes, cand)

    assert key in out["description_by_key"]
    description = out["description_by_key"][key]
    assert "X" in description
    assert "+0.00" in description
    assert "toward" in description
    assert "Y" in description
    assert not any(e.kind == "lag_response" for e in out["evidence"])


def test_description_signed_against_the_thesis_when_candidate_moved_the_wrong_way():
    """Same mechanism, opposite sign: X moves against the "up" thesis while
    Y still shocks toward it, so `x_signed < 0` and the description must say
    "against", not "toward" -- the disconfirming case for the previous
    test's sign branch.

    Reuses the deleted PHASE-3 block's "opposed" fixture
    (`_origin_move_closes([-0.05, -0.06, -0.04])`): `x_signed ==
    -3.512844`, computed directly against this fixture before writing this
    assertion (same z as the deleted
    `test_lag_response_opposed_when_candidate_moved_the_other_way`). Unlike
    the deleted `lag_response` unit, this never produces a contradicting
    `Evidence` -- `description` never produces any `Evidence` at all.

    Falsifies if: the description does not contain "against", or does not
    contain the negative signed value "-3.51".
    """
    closes = _origin_move_closes([-0.05, -0.06, -0.04])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)

    out = _run_origin_move_chain(closes, cand)

    description = out["description_by_key"][key]
    assert "against" in description
    assert "-3.51" in description
    assert not any(e.kind == "lag_response" for e in out["evidence"])


def test_description_absent_when_origin_leader_is_unset():
    """Corroboration-mode invariance guard: an `origin="external"` candidate
    never sets `origin_leader` (defaults to `None`), so the `description`
    mechanism must be a complete no-op for it -- no `description_by_key`
    entry.

    Reuses `_shocked_closes()`/`_candidate()` (default `origin_leader=None`)
    -- the same fixture
    `test_correlation_only_evidence_is_unchanged_by_the_supply_edge_fix`
    already pins for the ordinary `leader_move` evidence, so this also
    confirms the new mechanism does not disturb that pre-existing,
    unrelated evidence.

    Falsifies if: `description_by_key` gains an entry for this candidate's
    key (e.g. `None` matched against a literal column/string), or any
    `lag_response` Evidence appears.
    """
    closes = _shocked_closes()
    cand = _candidate(d=date(2026, 3, 26))
    key = candidate_key(cand)
    rt = _runtime(closes)
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]

    out = fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)

    assert key not in out["description_by_key"]
    assert not any(e.kind == "lag_response" for e in out["evidence"])


# --- D-86, kept: a missing candidate move is an explicit no-result --------


def _candidate_without_own_shock() -> tuple[str, Runtime[LagMatrixContext], dict]:
    """`X`'s `origin_leader` `Y` has a `Shock`; `X` itself does not -- the
    case D-86's guard exists for (`context_fusion.py`'s `cand_z = ... else
    0.0` fallback would otherwise let a missing candidate move masquerade as
    "moved by exactly zero" and be described as such).

    Built by handing `fuse_evidence` a `leader_shocks` dict directly
    (`{key: [Shock(symbol="Y", ...)]}`, no `Shock` for `X`) rather than
    routing through `leader_state`: `leader_state`'s `sym == c.symbol`
    clause gives the candidate a `Shock` of its own whenever `c.symbol` is a
    column of `returns`, so there is no ordinary market-data fixture where a
    normally-trading candidate ends up without one -- only a hand-built
    `leader_shocks` input reproduces "the shock is missing" honestly.
    `lag_edges=[]` so `leaders == []`; under the new gate (`if not leaders
    and not c.origin_leader: continue`) `c.origin_leader` being set alone is
    what keeps this candidate from being skipped outright -- whether `Y`
    itself resolves in `shocks` no longer matters to the gate.
    """
    closes = pd.DataFrame(
        {"X": [100.0, 101.0, 102.0], "Y": [50.0, 50.5, 51.0]},
        index=pd.bdate_range("2026-01-01", periods=3, tz="UTC"),
    )
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)
    shock_y = Shock(symbol="Y", pct_change=0.06, sigma=3.0, lookback_days=3, date=date(2026, 1, 10))
    rt = _runtime(closes)
    state = {"candidates": [cand], "lag_edges": [], "leader_shocks": {key: [shock_y]}}
    return key, rt, state


def test_description_skipped_when_candidates_own_shock_is_missing():
    """D-86, kept and adapted to `description` (D-87): when the candidate's
    own move is genuinely unknown (no `Shock` for `c.symbol`), the whole
    `description` mechanism must be skipped -- no `description_by_key`
    entry -- rather than silently reading the missing move as "moved by
    exactly zero" and describing it as such.

    Falsifies if: `description_by_key` gains an entry for this key.
    """
    key, rt, state = _candidate_without_own_shock()

    out = fuse_evidence(state, rt)

    assert key not in out["description_by_key"]


def test_description_missing_candidate_shock_is_reported_in_errors():
    """The skip above must be observable, not silent -- this repo's
    CLAUDE.md is explicit that a silent path must be given an explicit
    outcome. House style: `graph_retriever.retrieve_neighbourhood`'s
    `errors` list (`f"{c.symbol} {c.as_of}: <reason>"`, collected in a local
    list and returned under the `"errors"` key).

    Falsifies if: `errors` is empty, has a length other than 1, or its one
    message does not contain the candidate's symbol ("X").
    """
    key, rt, state = _candidate_without_own_shock()

    out = fuse_evidence(state, rt)

    assert len(out["errors"]) == 1
    assert "X" in out["errors"][0]


def test_description_never_emits_evidence():
    """`description` carries no vote and no weight (D-87) -- across every
    case that populates, or attempts to populate, `description_by_key`
    (candidate quiet, candidate moved against the thesis, candidate's own
    shock missing), `fuse_evidence` must never emit an `Evidence` with
    `kind == "lag_response"`; that unit no longer exists at all (Success
    Criterion 3). Duplicates the same check already made individually in
    the three tests above (per TASK-1.1's own instruction that each of them
    must assert it explicitly too, not just by omission), gathered here so
    a reader scanning for "does this ever vote" finds one test that answers
    it directly across every relevant branch.

    Falsifies if: any of the three cases below produces an `Evidence` with
    `kind == "lag_response"`.
    """
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )

    quiet = _run_origin_move_chain(_origin_move_closes([0.0, 0.0, 0.0]), cand)
    assert not any(e.kind == "lag_response" for e in quiet["evidence"])

    against = _run_origin_move_chain(_origin_move_closes([-0.05, -0.06, -0.04]), cand)
    assert not any(e.kind == "lag_response" for e in against["evidence"])

    _, rt, state = _candidate_without_own_shock()
    missing = fuse_evidence(state, rt)
    assert not any(e.kind == "lag_response" for e in missing["evidence"])
