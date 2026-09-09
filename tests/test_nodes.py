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


# --- PHASE-2: leader_state always resolves the origin leader's shock ------


def _origin_leader_closes() -> pd.DataFrame:
    """A hand-built universe isolating the `origin_leader` mechanism from the
    ordinary correlation-derived `leaders` list.

    Three columns: OTHERLEAD (X's only `lag_edges` leader, wired directly in
    the test rather than via `retrieve_neighbourhood`), X (the candidate),
    and Y (the candidate's `origin_leader` -- deliberately *not* one of X's
    `lag_edges`, so Y's symbol can reach `leader_shocks` only through the
    PHASE-2 mechanism, never through the pre-existing `leaders` list).

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


def test_leader_state_always_resolves_the_origin_leader_even_below_sigma():
    """PHASE-2: `leader_state` must emit a `Shock` for `c.origin_leader` even
    when its own `|z| < sigma` -- the same unconditional treatment the
    candidate's own symbol already gets (`sym == c.symbol`).

    Y is engineered to sit genuinely below the sigma=2.0 threshold: computed
    directly against `_origin_leader_closes()` with
    `shocks.standardised_moves` before writing this assertion, Y's z there is
    -0.3124 (`|z| = 0.31 < 2.0`). Y is also deliberately absent from
    `lag_edges` (only OTHERLEAD->X is wired) -- see `_origin_leader_closes`'s
    docstring -- so the only way Y's symbol can reach `leader_shocks` at all
    is through `c.origin_leader`, not through the ordinary `leaders` list.
    This pins both halves of TASK-2.2 (the `syms` extension and the `sigma`
    filter extension) in one assertion.

    Falsifies if: no `Shock` with `symbol == "Y"` appears in `leader_shocks`
    for this candidate -- either because `leader_state` never computed a `z`
    for Y at all (`syms` not extended), or computed one and discarded it
    below threshold (filter not extended).
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
    y_shocks = [s for s in out["leader_shocks"][key] if s.symbol == "Y"]
    assert len(y_shocks) == 1
    assert abs(y_shocks[0].sigma) < 2.0


def test_leader_state_does_not_raise_when_origin_leader_missing_from_closes():
    """PHASE-2's halt condition: if `c.origin_leader` names a symbol absent
    from `returns.columns` (e.g. delisted, or a data gap), `leader_state`
    must not raise -- no `z` can be computed for a column that doesn't
    exist, so that symbol simply produces no `Shock`, silently.

    Falsifies if: this raises (e.g. a `KeyError` from indexing `returns` on
    a missing column while extending `syms`), or a `Shock` for the missing
    symbol appears anyway.
    """
    closes = _origin_leader_closes()
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan",
        origin_leader="MISSING",
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

    out = leader_state({"candidates": [cand], "lag_edges": edges}, rt)  # must not raise

    key = candidate_key(cand)
    assert "MISSING" not in {s.symbol for s in out["leader_shocks"][key]}


def test_leader_state_external_candidate_unaffected_by_origin_leader_resolution():
    """Corroboration-mode invariance guard: an `origin="external"` candidate
    never sets `origin_leader` (PHASE-1 -- it defaults to `None`), so the
    PHASE-2 extension must be a no-op for it: exactly today's shocks, nothing
    added, nothing removed.

    Uses `_shocked_closes()` and the existing `_candidate()` helper (default
    `origin_leader=None`) -- the same fixture
    `test_fuse_evidence_effective_evidence_never_exceeds_raw_count` and
    `test_correlation_only_evidence_is_unchanged_by_the_supply_edge_fix`
    already rely on for their own invariance claims. LEAD1/LEAD2 clear
    sigma=2.0 on their own merits (computed directly beforehand: sigma =
    6.17 / 6.01) and CAND is included via the pre-existing `sym ==
    c.symbol` clause, so this exact three-symbol set is unrelated to the
    PHASE-2 change and must not move.

    Falsifies if: the symbol set gains or loses a member -- e.g. `None`
    being matched against a literal "None" column, or any other interaction
    between the new `or sym == c.origin_leader` clause and an unset
    `origin_leader`.
    """
    closes = _shocked_closes()
    cand = _candidate(d=date(2026, 3, 26))
    rt = _runtime(closes)
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]

    out = leader_state({"candidates": [cand], "lag_edges": edges}, rt)

    key = candidate_key(cand)
    assert {s.symbol for s in out["leader_shocks"][key]} == {"LEAD1", "LEAD2", "CAND"}


# --- PHASE-3: fuse_evidence classifies open / responded / opposed ---------


def _lag_response_closes(x_recent: list[float]) -> pd.DataFrame:
    """Two columns, Y (the candidate `X`'s `origin_leader`) and X itself --
    the minimal shape PHASE-4's halt condition recommends (no other
    neighbours to entangle with), mirroring `_origin_leader_closes` above
    and `test_market_scan.py`'s `_reentry_fixture` construction.

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


def _run_lag_response_chain(closes: pd.DataFrame, cand: Candidate) -> dict:
    """`retrieve_neighbourhood` -> `leader_state` -> `fuse_evidence`, the
    real chain every PHASE-3 test below runs, over `_lag_response_closes`'s
    `trail=10`/`move_win=3`/`sigma=2.0` context."""
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), trail=10, topk=20, move_win=3, sigma=2.0
        )
    )
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]
    return fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)


def test_lag_response_open_when_candidate_has_not_moved():
    """D3 "open", boundary case: `X` truly unmoved (`x_component == 0`) ->
    `room_by_key[key] == 1.0`, `origin_status_by_key[key] == "open"`, one
    `Evidence(kind="lag_response", symbol="Y", supports=True, weight=1.0)`.

    Computed directly against `_lag_response_closes([0.0, 0.0, 0.0])`
    (`retrieve_neighbourhood` -> `leader_state`) before writing this
    assertion: `leaders == ["Y"]` (the correlation edge that keeps
    `fuse_evidence`'s existing `if not leaders: continue` guard from
    skipping this candidate), Y's z (`y_component`, `want=+1` for "up") is
    `3.526436`, X's z is exactly `0.0` -- three genuinely zero returns in
    the `move_win` window, not a near-zero float (a `[0.001, -0.002,
    0.001]`-style offsetting fixture was tried first and left an
    IEEE-noise residual of `-1.03e-13`, which is `< 0` and would have
    misclassified as "opposed"; using literal zero returns avoids that
    trap). `x_component == 0.0` is therefore safely `>= 0` and `<
    y_component`, landing in "open" with `room == round(1 -
    0/3.526436, 4) == 1.0` exactly.

    Falsifies if: `room_by_key`/`origin_status_by_key` are absent (the keys
    TASK-3.2 must add), `room` is anything other than exactly `1.0`, or no
    `lag_response` `Evidence` is emitted with `supports=True`/`weight=1.0`.
    """
    closes = _lag_response_closes([0.0, 0.0, 0.0])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)

    fused = _run_lag_response_chain(closes, cand)

    assert fused["room_by_key"][key] == 1.0
    assert fused["origin_status_by_key"][key] == "open"
    lag_ev = [e for e in fused["evidence"] if e.kind == "lag_response"]
    assert len(lag_ev) == 1
    assert lag_ev[0].symbol == "Y"
    assert lag_ev[0].supports is True
    assert lag_ev[0].weight == 1.0


def test_lag_response_open_with_partial_room_when_candidate_partly_moved():
    """D3 "open", interior case: `X` has moved partway (`0 < x_component <
    y_component`) -> `0 < room < 1`, `origin_status == "open"`, same
    `Evidence` shape as the unmoved case (`supports=True`).

    Computed directly against `_lag_response_closes([0.005, 0.006, 0.004])`
    before writing this assertion: `leaders == ["Y"]`, `y_component ==
    3.526436`, `x_component == 3.318779`, giving `room == round(1 -
    3.318779/3.526436, 4) == 0.0589` -- comfortably inside `(0, 1)`, not a
    boundary value.

    Falsifies if: `room` is `<= 0`, `>= 1`, or `origin_status` is anything
    other than `"open"`; or the `lag_response` `Evidence` is missing or has
    `supports=False`.
    """
    closes = _lag_response_closes([0.005, 0.006, 0.004])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)

    fused = _run_lag_response_chain(closes, cand)

    room = fused["room_by_key"][key]
    assert 0 < room < 1
    assert fused["origin_status_by_key"][key] == "open"
    lag_ev = [e for e in fused["evidence"] if e.kind == "lag_response"]
    assert len(lag_ev) == 1
    assert lag_ev[0].symbol == "Y"
    assert lag_ev[0].supports is True
    assert lag_ev[0].weight == 1.0


def test_lag_response_responded_emits_no_evidence():
    """D3 "responded": `X` has moved at least as much as `Y`, same
    direction (`x_component >= y_component`) -> `origin_status ==
    "responded"`, `room == 0.0`, and **no** `Evidence` with `kind ==
    "lag_response"` anywhere in `evidence` -- the user's core distinction
    (D2): "already responded" must not be able to manufacture a
    corroborating or contradicting unit of its own.

    Computed directly against `_lag_response_closes([0.06, 0.07, 0.05])`
    before writing this assertion: `leaders == ["Y"]`, `y_component ==
    3.526436`, `x_component == 3.540641` -- `x_component >= y_component` by
    a clear margin (a deliberately non-identical raw jump, not a contrived
    exact tie, since `X`'s own quiet baseline differs from `Y`'s and an
    identical raw jump does not land exactly on the tie), landing in
    "responded".

    Falsifies if: `origin_status` is not `"responded"`, `room` is not
    exactly `0.0`, or any `Evidence` in `fused["evidence"]` has `kind ==
    "lag_response"` (whether `supports=True` or `False` -- either would be
    the exact conflation D2 exists to prevent).
    """
    closes = _lag_response_closes([0.06, 0.07, 0.05])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)

    fused = _run_lag_response_chain(closes, cand)

    assert fused["origin_status_by_key"][key] == "responded"
    assert fused["room_by_key"][key] == 0.0
    assert not any(e.kind == "lag_response" for e in fused["evidence"])


def test_lag_response_opposed_when_candidate_moved_the_other_way():
    """D3 "opposed": `X` moved against the thesis (`x_component < 0`) ->
    `origin_status == "opposed"`, `room is None` (not "zero room" -- the
    thesis is refuted, not merely spent, so no room figure applies), one
    `Evidence(kind="lag_response", symbol="Y", supports=False, weight=1.0)`.

    Computed directly against `_lag_response_closes([-0.05, -0.06, -0.04])`
    before writing this assertion: `leaders == ["Y"]`, `y_component ==
    3.526436`, `x_component == -3.512844` -- clearly negative, landing in
    "opposed".

    Falsifies if: `room_by_key[key]` is anything other than `None` (e.g. a
    numeric `0.0`, collapsing "opposed" into "responded"), `origin_status`
    is not `"opposed"`, or the `lag_response` `Evidence` is missing or has
    `supports=True`.
    """
    closes = _lag_response_closes([-0.05, -0.06, -0.04])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)

    fused = _run_lag_response_chain(closes, cand)

    assert fused["origin_status_by_key"][key] == "opposed"
    assert fused["room_by_key"][key] is None
    lag_ev = [e for e in fused["evidence"] if e.kind == "lag_response"]
    assert len(lag_ev) == 1
    assert lag_ev[0].symbol == "Y"
    assert lag_ev[0].supports is False
    assert lag_ev[0].weight == 1.0


def test_lag_response_absent_when_origin_leader_is_unset():
    """Corroboration-mode invariance guard (D2/D3): an `origin="external"`
    candidate never sets `origin_leader` (PHASE-1 -- `None` by default), so
    the whole PHASE-3 mechanism must be a no-op for it: no `room_by_key`/
    `origin_status_by_key` entry for its key, and no `lag_response`
    `Evidence` anywhere.

    Reuses `_shocked_closes()`/`_candidate()` (default `origin_leader=None`,
    `origin="external"`) -- the same fixture
    `test_correlation_only_evidence_is_unchanged_by_the_supply_edge_fix`
    already pins for the ordinary `leader_move` evidence, so this test also
    confirms the new mechanism does not disturb that pre-existing,
    unrelated evidence (the PHASE-3 halt condition's concern).

    Falsifies if: `room_by_key`/`origin_status_by_key` gain a non-`None`
    entry for this candidate's key (e.g. `None` being matched against a
    literal column, per PHASE-2's analogous guard), or any `Evidence` with
    `kind == "lag_response"` appears.
    """
    closes = _shocked_closes()
    cand = _candidate(d=date(2026, 3, 26))
    key = candidate_key(cand)
    rt = _runtime(closes)
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]

    fused = fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)

    assert fused["room_by_key"].get(key) is None
    assert fused["origin_status_by_key"].get(key) is None
    assert not any(e.kind == "lag_response" for e in fused["evidence"])


# --- Fix: candidate's own shock missing must not masquerade as "open" -----


def _candidate_without_own_shock() -> tuple[str, Runtime[LagMatrixContext], dict]:
    """`X`'s `origin_leader` `Y` has a `Shock`; `X` itself does not -- the
    defect case (`context_fusion.py`'s `cand_z = ... else 0.0` fallback,
    read together with the `x_component == 0` -> "open"/`room=1.0` branch).

    Built by handing `fuse_evidence` a `leader_shocks` dict directly
    (`{key: [Shock(symbol="Y", ...)]}`, no `Shock` for `X`) rather than
    routing through `leader_state`: `leader_state`'s `sym == c.symbol`
    clause gives the candidate a `Shock` of its own whenever `c.symbol` is a
    column of `returns`, so there is no ordinary market-data fixture where a
    normally-trading candidate ends up without one -- only a hand-built
    `leader_shocks` input reproduces "the shock is missing" honestly, per
    the assignment. `lag_edges=[]` so `leaders == []` and the pre-existing
    `leader_move` loop (unrelated, must not move -- see D-84) never runs;
    `c.origin_leader in shocks` alone is what keeps `fuse_evidence`'s `if
    not leaders and not (...): continue` guard from skipping the candidate
    outright, exactly as it does for every other PHASE-3 test above.
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


def test_lag_response_skipped_when_candidates_own_shock_is_missing():
    """The defect: `origin_leader` (`Y`) resolves in `shocks` but the
    candidate's OWN symbol (`X`) does not, so `cand_z` silently falls back
    to `0.0`. Under D-84's classification that reads as "candidate moved by
    exactly zero" -- the strongest possible "open" signal (`room=1.0`) --
    when the truth is "we don't know if it moved". The whole `lag_response`
    block must be skipped instead: no `room_by_key`/`origin_status_by_key`
    entry for this key, and no `lag_response` Evidence anywhere.

    Today this fails: `room_by_key[key] == 1.0` and
    `origin_status_by_key[key] == "open"` with one `lag_response` Evidence
    emitted -- identical to the deliberate "truly unmoved" case above,
    because nothing currently distinguishes "moved by exactly zero" from
    "unknown".

    Falsifies if: `room_by_key`/`origin_status_by_key` gain any entry for
    this key, or a `lag_response` Evidence appears in `fused["evidence"]`.
    """
    key, rt, state = _candidate_without_own_shock()

    fused = fuse_evidence(state, rt)

    assert key not in fused["room_by_key"]
    assert key not in fused["origin_status_by_key"]
    assert not any(e.kind == "lag_response" for e in fused["evidence"])


def test_lag_response_missing_candidate_shock_is_reported_in_errors():
    """The skip above must be observable, not silent -- this repo's
    CLAUDE.md is explicit that a silent path must be given an explicit
    outcome. House style: `graph_retriever.retrieve_neighbourhood`'s
    `errors` list (`f"{c.symbol} {c.as_of}: <reason>"`, collected in a local
    list and returned under the `"errors"` key) -- `fuse_evidence` does not
    return that key at all yet.

    Today this fails with `KeyError: 'errors'`.

    Falsifies if: `"errors"` is absent, has a length other than 1, or its
    one message does not contain the candidate's symbol ("X").
    """
    key, rt, state = _candidate_without_own_shock()

    fused = fuse_evidence(state, rt)

    assert len(fused["errors"]) == 1
    assert "X" in fused["errors"][0]


def test_lag_response_ordinary_case_unaffected_by_the_missing_shock_fix():
    """Invariance guard: when the candidate DOES have its own `Shock` (the
    ordinary case every PHASE-3 test above exercises), the fix must be a
    complete no-op -- same `room`/`origin_status`/`lag_response` Evidence as
    before the fix, and no error reported. Reuses the existing
    `_lag_response_closes`/`_run_lag_response_chain` fixture (the "open,
    unmoved" case from `test_lag_response_open_when_candidate_has_not_moved`)
    rather than a new one, per the assignment. `fused.get("errors", [])` so
    this passes both before the fix (no `"errors"` key at all) and after
    (an empty one) -- it is already expected to pass today, not forced red.

    Falsifies if: `room`/`origin_status`/the `lag_response` Evidence differ
    from `test_lag_response_open_when_candidate_has_not_moved`'s values, or
    any error is reported for this candidate.
    """
    closes = _lag_response_closes([0.0, 0.0, 0.0])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )
    key = candidate_key(cand)

    fused = _run_lag_response_chain(closes, cand)

    assert fused["room_by_key"][key] == 1.0
    assert fused["origin_status_by_key"][key] == "open"
    lag_ev = [e for e in fused["evidence"] if e.kind == "lag_response"]
    assert len(lag_ev) == 1
    assert lag_ev[0].symbol == "Y"
    assert lag_ev[0].supports is True
    assert lag_ev[0].weight == 1.0
    assert fused.get("errors", []) == []
