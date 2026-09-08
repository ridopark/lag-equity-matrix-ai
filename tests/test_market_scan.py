"""PHASE-1 and PHASE-2 of PLAN-2026-09-08-market-scan: the shock sweep over
the whole universe (REQ-2, REQ-7), then traversal, dedup, and candidate
emission (REQ-1, REQ-3, REQ-4, REQ-5).

`shocked_leaders` must satisfy the "Dates: exactly what flows where" section
of the plan literally: `recent` is the `move_win` sessions ending at `as_of`,
`baseline` is the `trail` sessions strictly before `recent` begins (no
overlap) -- the opposite of `leader_state.py`'s overlapping convention, which
this plan deliberately does not mirror.

`candidates()` must traverse each shocked leader via
`topology.laggers_of(leader, max_hops, as_of)` (D-73/D-79), dedup a lagger
reached by more than one leader to the larger-`abs(z)` leader (REQ-4), and
inherit that leader's shock sign as the candidate's direction (REQ-5).

PHASE-3 (REQ-6) adds one test proving the point-in-time guarantee against a
**real** ArangoDB rather than `FakeArangoTopology` above, which would happily
return whatever edges it is told to regardless of `as_of` and so cannot prove
anything about date filtering (D-82: `NewsIndex.search` passed 80 tests with
no date filter at all before a reviewer caught a 24-day lookahead leak by
reading retrieved output, not by the suite). The plan predicts this needs no
new production code: `MarketScan.candidates` delegates `as_of` straight
through to `ArangoTopology.laggers_of`'s existing ALL-quantified per-path
`filing_date <= @as_of` guard. This test is what would falsify that
prediction. Fixture pattern copied verbatim from `test_arango_topology.py`
(module-scoped, disposable database, TCP-probe skip, `equity`/`supplies_to`
collections) -- kept in this file rather than a new one since it exercises
the same `MarketScan.candidates` seam as PHASE-1/2 above, just with a real
`topology` in place of the fake.

PHASE-4 (REQ-7) closes the "leader re-entry hole" the plan's review found:
`context_fusion` already refuses to score a candidate `X` on its own supply
edge (D-81), but `graph_retriever.py`'s correlation pool only drops
`signal_universe`, `excluded_symbols`, and `X` itself -- not the *leader*
`Y` that got `X` discovered in the first place. If `Y` lands in `X`'s
correlation top-k (plausible for a customer/supplier pair), `Y`'s shock
re-enters as ordinary `leader_move` evidence, laundering the same
circularity D-81 was built to block, one hop removed. One test guards that
`shocked_leaders()` (the public surface a caller would use to build the
`signal_universe` exclusion) can never silently diverge from the leaders
`candidates()` actually traversed; the other proves, through the real graph
(`build_graph`, not a unit stub -- the point is what `context_fusion` ends
up counting), that once `Y` is unioned into `signal_universe` the re-entry
evidence disappears and the candidate correctly reaches `END` with no
`Assessment` via the pre-existing D-27 no-neighbourhood route, rather than a
weakened one.
"""

from __future__ import annotations

import os
import socket
from datetime import date
from unittest import mock
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import pytest

from lagmatrix.adapters.arango import ArangoTopology
from lagmatrix.adapters.candidates import MarketScan
from lagmatrix.domain.models import Candidate, LagEdge
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.shocks import standardised_moves

ARANGO_URL = os.environ.get("LAGMATRIX_ARANGO_URL", "http://localhost:8529")
ARANGO_USER = os.environ.get("LAGMATRIX_ARANGO_USER", "root")
ARANGO_PASSWORD = os.environ.get("LAGMATRIX_ARANGO_PASSWORD", "")

# A disposable database of its own -- never the real `lagmatrix` -- distinct
# from `test_arango_topology.py`'s own disposable database of the same shape,
# so the two test files' fixture data cannot collide.
ARANGO_DB_NAME = "test_market_scan"
VERTEX = "equity"
EDGE = "supplies_to"

TRAIL = 60
MOVE_WIN = 3
SIGMA = 2.0


def _shock_fixture(rng_seed: int = 0) -> tuple[pd.DataFrame, date]:
    """4-column universe over `TRAIL + MOVE_WIN + 5` sessions (TASK-1.1).

    Every column is small seeded Gaussian noise. `LEADUP` and `ETF1` each get
    an identical +0.20 jump added to the `MOVE_WIN` sessions ending at
    `as_of` (cumulative >> the ~0.001-scale baseline noise regardless of
    seed); `LEADDOWN` gets the mirror -0.20 jump over the same sessions;
    `FLAT` gets no jump at all. The 5 sessions after `as_of` are pure buffer
    so a "later" session always exists (mirrors `graph_retriever.py`'s
    point-in-time idiom).

    `as_of` sits at session index `TRAIL + MOVE_WIN - 1` so that, per the
    plan's windowing, `recent = returns.iloc[TRAIL : TRAIL + MOVE_WIN]` is
    exactly the jumped rows and `baseline = returns.iloc[0 : TRAIL]` is pure
    noise -- the same windows asserted against directly in the tests below.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(rng_seed)
    data = {}
    for col, jump in [("LEADUP", 0.20), ("LEADDOWN", -0.20), ("FLAT", 0.0), ("ETF1", 0.20)]:
        r = rng.normal(0, 0.001, n)
        r[TRAIL : TRAIL + MOVE_WIN] += jump
        data[col] = 100 * np.exp(np.cumsum(r))
    closes = pd.DataFrame(data, index=idx)
    as_of = idx[TRAIL + MOVE_WIN - 1].date()
    return closes, as_of


def _overlap_fixture() -> tuple[pd.DataFrame, date]:
    """A second universe (TASK-1.4): a single column, `JUMPY`, whose only
    engineered jump sits in the `MOVE_WIN` sessions immediately before
    `as_of` -- exactly the window a `leader_state.py`-style overlapping
    baseline (`returns.iloc[ti-trail:ti]`) would wrongly include, and nowhere
    else. Same shape as `_shock_fixture` otherwise.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.001, n)
    r[TRAIL : TRAIL + MOVE_WIN] += 0.20
    closes = pd.DataFrame({"JUMPY": 100 * np.exp(np.cumsum(r))}, index=idx)
    as_of = idx[TRAIL + MOVE_WIN - 1].date()
    return closes, as_of


def test_shocked_leaders_finds_the_engineered_up_and_down_moves():
    """Falsifies if: `shocked_leaders` misses either engineered symbol
    (`LEADUP`/`LEADDOWN` absent, or their sign/magnitude wrong), or includes
    `FLAT`, which was never jumped.
    """
    closes, as_of = _shock_fixture()
    scan = MarketScan(closes, topology=None, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)
    result = scan.shocked_leaders(as_of)

    returns = closes.pct_change()
    baseline = returns.iloc[0:TRAIL]
    recent = returns.iloc[TRAIL : TRAIL + MOVE_WIN]
    expected = standardised_moves(recent, ["LEADUP", "LEADDOWN"], MOVE_WIN, baseline)

    assert result["LEADUP"] == pytest.approx(float(expected["LEADUP"]))
    assert result["LEADDOWN"] == pytest.approx(float(expected["LEADDOWN"]))
    assert result["LEADUP"] > 0
    assert result["LEADDOWN"] < 0
    assert abs(result["LEADUP"]) >= SIGMA
    assert abs(result["LEADDOWN"]) >= SIGMA
    assert "FLAT" not in result


def test_shocked_leaders_excludes_funds_even_when_shocked():
    """Falsifies if: `ETF1` appears in the result despite moving identically
    to `LEADUP` (D-62 not applied to the leader/scan side).
    """
    closes, as_of = _shock_fixture()
    scan = MarketScan(
        closes,
        topology=None,
        excluded_symbols=frozenset({"ETF1"}),
        trail=TRAIL,
        move_win=MOVE_WIN,
        sigma=SIGMA,
    )
    result = scan.shocked_leaders(as_of)

    assert "ETF1" not in result
    assert "LEADUP" in result  # sanity: exclusion did not also swallow the real leader


def test_shocked_leaders_returns_empty_with_insufficient_history():
    """Falsifies if: this raises (negative `.iloc` slicing wrapping around)
    instead of returning `{}` when `ti < move_win + trail`.
    """
    closes, _ = _shock_fixture()
    as_of = closes.index[1].date()  # ti will be 2, far short of move_win + trail
    scan = MarketScan(closes, topology=None, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)

    assert scan.shocked_leaders(as_of) == {}


def test_shocked_leaders_baseline_excludes_the_recent_window():
    """Falsifies if: the baseline and recent windows overlap -- i.e. the
    implementation copied `leader_state.py`'s convention
    (`returns.iloc[ti-trail:ti]`) instead of REQ-2's stricter, non-overlapping
    one (`returns.iloc[ti-move_win-trail:ti-move_win]`).
    """
    closes, as_of = _overlap_fixture()
    scan = MarketScan(closes, topology=None, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)

    with mock.patch("lagmatrix.shocks.standardised_moves", wraps=standardised_moves) as spy:
        result = scan.shocked_leaders(as_of)

    # The shock is still correctly detected...
    assert result["JUMPY"] > 0
    assert abs(result["JUMPY"]) >= SIGMA

    # ...but only because the baseline actually used excludes the jumped rows.
    spy.assert_called_once()
    call = spy.call_args
    baseline_arg = call.kwargs.get("baseline")
    if baseline_arg is None:
        baseline_arg = call.args[3]
    expected_baseline = closes.pct_change().iloc[0:TRAIL]
    pd.testing.assert_frame_equal(baseline_arg, expected_baseline)


class FakeArangoTopology:
    """Fake `ArangoTopology`-shaped adapter, mirroring `FlakyNewsIndex`'s
    fake-adapter pattern in `test_news_node.py`. Owned by this file rather
    than imported from `test_nodes.py` (each test file owns its fakes here,
    per TASK-2.1). Returns canned `LagEdge`s from `laggers_of` and records
    every call's arguments so tests can assert on them without touching a
    real ArangoDB.
    """

    def __init__(self, edges_by_leader: dict[str, list[LagEdge]]):
        self._edges_by_leader = edges_by_leader
        self.calls: list[tuple[str, int, date]] = []

    def laggers_of(self, leader: str, max_hops: int, as_of: date) -> list[LagEdge]:
        self.calls.append((leader, max_hops, as_of))
        return self._edges_by_leader.get(leader, [])


def _lag_edge(leader: str, lagger: str) -> LagEdge:
    """Filler `LagEdge` -- these tests exercise `candidates()`'s traversal
    and dedup logic, not the correlation/beta fields, which are never
    inspected below.
    """
    return LagEdge(
        leader=leader, lagger=lagger, correlation=0.4, lag_days=1, beta=0.2, relation="supplier"
    )


def test_candidates_emits_one_per_lagger_with_scan_origin():
    """Falsifies if: `direction` is wrong, `origin` is not `"scan"`, or
    `SUP1` is missing/duplicated -- i.e. if traversal or candidate
    construction from a shocked leader's reached lagger is broken. Also
    falsifies if `origin_leader` is not `"LEADUP"` -- PHASE-1's field must be
    populated with the claiming leader, not left at its `None` default.

    The explicit `.origin_leader` assertion is load-bearing, not redundant
    with the equality check below: pydantic's default `extra="ignore"`
    silently drops an undeclared `origin_leader=` kwarg on *both* sides of
    the equality before the field exists on `Candidate`, so the equality
    alone would pass vacuously today regardless of what this test is meant
    to pin.
    """
    closes, as_of = _shock_fixture()
    fake = FakeArangoTopology({"LEADUP": [_lag_edge("LEADUP", "SUP1")], "LEADDOWN": []})
    scan = MarketScan(closes, fake, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)

    result = scan.candidates(as_of)

    assert result[0].origin_leader == "LEADUP"
    assert result == [
        Candidate(
            symbol="SUP1", direction="up", as_of=as_of, origin="scan", origin_leader="LEADUP"
        )
    ]


def test_candidates_records_the_claiming_leader_as_origin_leader():
    """PHASE-1/TASK-1.1: `origin_leader` must name the specific leader whose
    move produced this candidate (D1) -- the relationship the later
    `lag_response` evidence (PHASE-3) will check, not merely a truthy
    provenance flag.

    Falsifies if: `origin_leader` is `None`, or is any symbol other than
    `"LEADUP"` (e.g. the lagger's own symbol, or a hardcoded placeholder).
    """
    closes, as_of = _shock_fixture()
    fake = FakeArangoTopology({"LEADUP": [_lag_edge("LEADUP", "SUP1")], "LEADDOWN": []})
    scan = MarketScan(closes, fake, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)

    result = scan.candidates(as_of)

    assert result[0].origin_leader == "LEADUP"


def test_external_signals_never_sets_origin_leader(tmp_path):
    """D1/PHASE-1: corroboration mode must be untouched by this field --
    `ExternalSignals.candidates()` never learns of a claiming leader, so
    `origin_leader` must stay at its `None` default for every candidate it
    emits.

    Falsifies if: `origin_leader` is anything other than `None` for an
    `ExternalSignals`-sourced candidate (e.g. a future refactor accidentally
    threading a leader-shaped value, such as the row's own ticker, through
    the shared `Candidate` constructor).
    """
    from lagmatrix.adapters.candidates import ExternalSignals

    path = tmp_path / "fires.csv"
    with path.open("w") as fh:
        fh.write("ticker,posted_at,direction\n")
        fh.write("SYNA,2026-05-29,up\n")

    [cand] = ExternalSignals(path).candidates()

    assert cand.origin == "external"
    assert cand.origin_leader is None


def test_candidates_reads_max_hops_and_as_of_from_self():
    """Falsifies if: `max_hops` is hardcoded instead of read from
    `self.max_hops`, or the `as_of` passed to `laggers_of` differs from the
    one passed to `candidates` -- either would show up as a wrong call tuple
    below.
    """
    closes, as_of = _shock_fixture()
    fake = FakeArangoTopology({})
    scan = MarketScan(
        closes,
        fake,
        excluded_symbols=frozenset({"ETF1"}),
        trail=TRAIL,
        move_win=MOVE_WIN,
        sigma=SIGMA,
        max_hops=3,
    )

    scan.candidates(as_of)

    # LEADUP (|z|~420) and LEADDOWN (|z|~315) are unambiguously separated in
    # magnitude (see PHASE-2's halt condition) -- ETF1 excluded leaves
    # exactly these two, visited largest-|z|-first.
    assert fake.calls == [("LEADUP", 3, as_of), ("LEADDOWN", 3, as_of)]


def test_candidates_dedups_by_larger_abs_z_leader():
    """Falsifies if: two `Candidate("SHARED", ...)` appear in the result, or
    the surviving one's direction comes from the smaller-`|z|` leader
    (`LEADDOWN`) instead of the larger one (`LEADUP`) -- and falsifies if the
    winner flips depending on the fake's internal dict insertion order. Also
    falsifies if `origin_leader` names the loser (`LEADDOWN`) instead of the
    larger-`|z|` winner (`LEADUP`) in either edge-map ordering.

    The explicit `.origin_leader` assertion is load-bearing for the same
    reason noted in `test_candidates_emits_one_per_lagger_with_scan_origin`:
    pydantic silently drops the undeclared kwarg on both sides of the
    equality before the field exists, so the equality alone cannot pin this.
    """
    closes, as_of = _shock_fixture()
    edges = {
        "LEADUP": [_lag_edge("LEADUP", "SHARED")],
        "LEADDOWN": [_lag_edge("LEADDOWN", "SHARED")],
    }
    reversed_edges = {"LEADDOWN": edges["LEADDOWN"], "LEADUP": edges["LEADUP"]}
    expected = [
        Candidate(
            symbol="SHARED", direction="up", as_of=as_of, origin="scan", origin_leader="LEADUP"
        )
    ]

    for edge_map in (edges, reversed_edges):
        fake = FakeArangoTopology(edge_map)
        scan = MarketScan(
            closes,
            fake,
            excluded_symbols=frozenset({"ETF1"}),
            trail=TRAIL,
            move_win=MOVE_WIN,
            sigma=SIGMA,
        )
        result = scan.candidates(as_of)

        assert result[0].origin_leader == "LEADUP"
        assert result == expected
        assert len([c for c in result if c.symbol == "SHARED"]) == 1


def test_candidates_drops_excluded_laggers():
    """Falsifies if: `BADETF` leaks into the result (D-62 not applied to the
    lagger/scan-reached side), while a genuine, non-excluded lagger from the
    same leader is correctly present.
    """
    closes, as_of = _shock_fixture()
    fake = FakeArangoTopology(
        {"LEADUP": [_lag_edge("LEADUP", "BADETF"), _lag_edge("LEADUP", "SUP1")], "LEADDOWN": []}
    )
    scan = MarketScan(
        closes,
        fake,
        excluded_symbols=frozenset({"ETF1", "BADETF"}),
        trail=TRAIL,
        move_win=MOVE_WIN,
        sigma=SIGMA,
    )

    result = scan.candidates(as_of)

    symbols = [c.symbol for c in result]
    assert "BADETF" not in symbols
    assert "SUP1" in symbols


def test_candidates_sorted_by_symbol():
    """Falsifies if: the output order tracks discovery order (`ZZZ` before
    `AAA`, since `LEADUP` is traversed before `LEADDOWN`) instead of being
    sorted by symbol ascending.
    """
    closes, as_of = _shock_fixture()
    fake = FakeArangoTopology(
        {"LEADUP": [_lag_edge("LEADUP", "ZZZ")], "LEADDOWN": [_lag_edge("LEADDOWN", "AAA")]}
    )
    scan = MarketScan(
        closes,
        fake,
        excluded_symbols=frozenset({"ETF1"}),
        trail=TRAIL,
        move_win=MOVE_WIN,
        sigma=SIGMA,
    )

    result = scan.candidates(as_of)

    assert [c.symbol for c in result] == ["AAA", "ZZZ"]


def test_candidates_empty_when_nothing_shocked():
    """Falsifies if: `laggers_of` is called despite there being zero shocked
    leaders (wasted traversal), or a non-empty result appears from nothing.
    """
    closes, _ = _shock_fixture()
    as_of = closes.index[1].date()  # same insufficient-history date as TASK-1.3
    fake = FakeArangoTopology({})
    scan = MarketScan(closes, fake, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)

    result = scan.candidates(as_of)

    assert result == []
    assert fake.calls == []


def _single_leader_shock_fixture() -> tuple[pd.DataFrame, date]:
    """One-column universe (`SHOCKLEAD`) for PHASE-3: same construction as
    `_shock_fixture`, engineered +0.20 jump over the `MOVE_WIN` sessions
    ending exactly at `as_of`, so `MarketScan` finds exactly one shocked
    leader to traverse via the real `topology` fixture below.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(2)
    r = rng.normal(0, 0.001, n)
    r[TRAIL : TRAIL + MOVE_WIN] += 0.20
    closes = pd.DataFrame({"SHOCKLEAD": 100 * np.exp(np.cumsum(r))}, index=idx)
    as_of = idx[TRAIL + MOVE_WIN - 1].date()
    return closes, as_of


# EARLYFILED's filing predates every possible `as_of` this fixture can
# produce (the `closes` index starts 2026-01-01); LATEFILED's postdates it by
# years -- "well before"/"well after" per TASK-3.1. No need to thread the two
# filings tightly around a specific `as_of` the way `test_arango_topology.py`'s
# BETWEEN_FILINGS does -- this test proves `MarketScan` *delegates* to the
# guard, not the guard's own ALL-quantified per-path semantics (already
# proven there).
EARLY_FILING = date(2020, 1, 1)
LATE_FILING = date(2030, 1, 1)


@pytest.fixture(scope="module")
def topology():
    """Connect to a real ArangoDB and seed one leader (`SHOCKLEAD`) with two
    suppliers -- `EARLYFILED` (filed well before any `as_of` this file uses)
    and `LATEFILED` (filed well after) -- in a disposable database. Skips
    cleanly if nothing is reachable, exactly like `test_arango_topology.py`.
    """
    arango = pytest.importorskip("arango")

    # Fast TCP probe before a real connection attempt -- python-arango's own
    # retry/backoff on an unreachable host costs ~54s per file otherwise.
    parsed = urlparse(ARANGO_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 8529), timeout=1):
            pass
    except OSError as exc:
        pytest.skip(f"ArangoDB not reachable at {ARANGO_URL}: {exc}")
    try:
        client = arango.ArangoClient(hosts=ARANGO_URL)
        sys_db = client.db("_system", username=ARANGO_USER, password=ARANGO_PASSWORD, verify=True)
    except Exception as exc:
        pytest.skip(f"ArangoDB not reachable at {ARANGO_URL}: {exc}")

    if not sys_db.has_database(ARANGO_DB_NAME):
        sys_db.create_database(ARANGO_DB_NAME)
    db = client.db(ARANGO_DB_NAME, username=ARANGO_USER, password=ARANGO_PASSWORD)

    if not db.has_collection(VERTEX):
        db.create_collection(VERTEX)
    if not db.has_collection(EDGE):
        db.create_collection(EDGE, edge=True)
    vertices = db.collection(VERTEX)
    edges = db.collection(EDGE)
    vertices.truncate()
    edges.truncate()

    for symbol in ["SHOCKLEAD", "EARLYFILED", "LATEFILED"]:
        vertices.insert({"_key": symbol})
    edges.insert(
        {
            "_from": f"{VERTEX}/EARLYFILED",
            "_to": f"{VERTEX}/SHOCKLEAD",
            "filing_date": EARLY_FILING.isoformat(),
            "pct_revenue": "20.0",
        }
    )
    edges.insert(
        {
            "_from": f"{VERTEX}/LATEFILED",
            "_to": f"{VERTEX}/SHOCKLEAD",
            "filing_date": LATE_FILING.isoformat(),
            "pct_revenue": "15.0",
        }
    )

    return ArangoTopology(db)


def test_point_in_time_excludes_a_lagger_filed_after_as_of(topology):
    """REQ-6, proven against a real ArangoDB rather than
    `FakeArangoTopology`: `SHOCKLEAD` is the only shocked leader; it has two
    suppliers, `EARLYFILED` (filed before `as_of`) and `LATEFILED` (filed
    after). `MarketScan.candidates(as_of)` must include `EARLYFILED` and
    exclude `LATEFILED`.

    Falsifies if: `LATEFILED` appears (a filing dated after `as_of`
    influenced the result -- the exact failure mode D-82 found in the vector
    adapter, relocated to this adapter), or if `EARLYFILED` is *also* missing
    (a false-negative that would hide the real bug behind an unrelated wiring
    error -- both are asserted, not just the absent one).
    """
    closes, as_of = _single_leader_shock_fixture()
    scan = MarketScan(closes, topology, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)

    result = scan.candidates(as_of)

    symbols = [c.symbol for c in result]
    assert "EARLYFILED" in symbols
    assert "LATEFILED" not in symbols


def test_shocked_leaders_matches_the_leaders_candidates_actually_queried():
    """TASK-4.1: a consistency guard, not new behaviour. `shocked_leaders`
    and `candidates` must agree on exactly which leaders were traversed --
    PHASE-5 will build `signal_universe`'s leader half from
    `shocked_leaders()`'s keys, so if that ever diverged from the leaders
    `candidates()` actually called `topology.laggers_of` on, the exclusion
    set built from the public method would silently miss (or over-include)
    leaders relative to what traversal really used.

    Falsifies if: `set(scan.shocked_leaders(as_of))` and the set of leaders
    recorded in `fake.calls` are ever different -- e.g. a future change
    filters leaders inside `candidates()` (say, dropping ones with no
    topology edges, or a symbol missing from `closes`) without teaching
    `shocked_leaders()` the identical filter.
    """
    closes, as_of = _shock_fixture()
    fake = FakeArangoTopology({"LEADUP": [_lag_edge("LEADUP", "SUP1")], "LEADDOWN": []})
    scan = MarketScan(
        closes,
        fake,
        excluded_symbols=frozenset({"ETF1"}),
        trail=TRAIL,
        move_win=MOVE_WIN,
        sigma=SIGMA,
    )

    scan.candidates(as_of)

    assert set(scan.shocked_leaders(as_of)) == {call[0] for call in fake.calls}


def _reentry_fixture(rng_seed: int = 7) -> tuple[pd.DataFrame, date]:
    """Two-column universe for TASK-4.2: `Y` (the would-be originating
    leader) gets the same engineered +0.20 jump as `_shock_fixture`'s
    `LEADUP`/`ETF1`, over the same `MOVE_WIN` sessions ending at `as_of`;
    `X` (standing in for a `MarketScan`-discovered lagger) is a
    deterministic linear function of `Y`'s own per-session returns
    (`0.8 * Y_returns` plus much smaller independent noise), not a fresh rng
    draw that merely happens to correlate.

    `X` and `Y` are the *only* two columns. `graph_retriever.py` always
    drops the candidate's own symbol from its correlation pool (Q-26), so
    without the PHASE-4/5 fix the pool has exactly one column left -- `Y` --
    which therefore lands in `X`'s top-k with certainty, not merely high
    probability: there is no other column for a chance correlation to hide
    behind, and (this is what makes the "reaches END" assertion provable
    rather than approximate) none left for the pool to fall back on once
    `Y` is excluded either -- the plan's halt condition about an "unrelated
    spurious neighbour" cannot arise in a two-column universe.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(rng_seed)
    ry = rng.normal(0, 0.001, n)
    ry[TRAIL : TRAIL + MOVE_WIN] += 0.20
    rx = 0.8 * ry + rng.normal(0, 0.0002, n)
    closes = pd.DataFrame(
        {"Y": 100 * np.exp(np.cumsum(ry)), "X": 100 * np.exp(np.cumsum(rx))}, index=idx
    )
    as_of = idx[TRAIL + MOVE_WIN - 1].date()
    return closes, as_of


def test_unioning_the_originating_leader_into_signal_universe_closes_the_reentry_hole():
    """TASK-4.2, run through the real graph (`build_graph`) rather than a
    unit stub, because the point is what `context_fusion` ends up counting.

    First proves the hole is real *given this fixture* -- not assumed --
    with `signal_universe` empty: `X`'s only neighbour is `Y` (see
    `_reentry_fixture`'s docstring for why that is guaranteed rather than
    probabilistic), `Y` is shocked, and `context_fusion` counts a
    `leader_move` Evidence for `Y` that reaches an `Assessment`. Then
    applies PHASE-5's fix directly -- `Y` unioned into `signal_universe`
    exactly as `X` itself already would be -- and proves it closes the
    hole: (a) no `Y`-attributed Evidence appears anywhere in the run, and
    (b) `X`'s correlation pool is now empty (both its only column, `Y`, and
    itself are dropped), so `route_on_neighbourhood` sends the branch
    straight to `END` (the pre-existing D-27 no-neighbourhood path,
    `graph/builder.py:90,106`; `assessor.py:26-27` skips any key absent from
    `effective_evidence_by_key`) and the run produces no `Assessment` for
    `X` at all.

    Falsifies if: `Y`'s move still appears as Evidence (anywhere in
    `evidence` or `evidence_by_key`) despite `Y` being in `signal_universe`
    -- the fix did not close the hole -- or if the run raises, hangs, or
    still produces an `Assessment` for `X` -- a hazard the fix introduced,
    not the pre-existing empty-neighbourhood route being exercised as-is.
    """
    closes, as_of = _reentry_fixture()
    candidate = Candidate(symbol="X", direction="up", as_of=as_of, origin="scan")
    g = build_graph(with_news=False)

    # Without the fix, given this fixture: the hole is real.
    hole = g.invoke(
        {"candidates": [candidate]},
        context=LagMatrixContext(closes=closes, signal_universe=set()),
    )
    assert {e.leader for e in hole["lag_edges"]} == {"Y"}
    assert any(e.kind == "leader_move" and e.symbol == "Y" for e in hole["evidence"])
    assert len(hole["assessments"]) == 1

    # With the fix: Y joins signal_universe alongside X itself (PHASE-5).
    fixed = g.invoke(
        {"candidates": [candidate]},
        context=LagMatrixContext(closes=closes, signal_universe={"Y"}),
    )
    assert fixed["lag_edges"] == []
    assert not any(e.symbol == "Y" for e in fixed.get("evidence", []))
    assert not any(
        e.symbol == "Y" for edges in fixed.get("evidence_by_key", {}).values() for e in edges
    )
    assert fixed.get("assessments") in (None, [])
