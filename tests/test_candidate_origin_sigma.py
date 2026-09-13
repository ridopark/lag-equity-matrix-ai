"""Amendment to PLAN-2026-09-11-quant-daytrade-perspectives PHASE-3:
`cap_for_llm`'s sigma-ordering branch was unreachable, because `Candidate`
carried no `sigma`-shaped field and `MarketScan.candidates()` computes each
leader's z-score only to sort by it (`adapters/candidates.py:121`), then
discards it at the final `sorted(claims.values(), key=lambda c: c.symbol)`
(line 136) -- capping to `max_llm_candidates` would silently prefer
alphabetically-early names over the names that actually shocked.

These tests pin the fix as purely additive, mirroring `test_candidate_
timestamp.py`'s `as_of_ts` precedent: `Candidate` gains an optional
`origin_sigma: float | None = None`; `MarketScan.candidates()` populates it
with the **signed** z of the leader that claimed each lagger (not `abs()` --
`cap_for_llm` applies that itself, see `test_llm_adapter.py`); and
`ExternalSignals` leaves it `None`, since alert-fed candidates have no shock
z to report.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from lagmatrix.adapters.candidates import ExternalSignals, MarketScan
from lagmatrix.domain.models import Candidate, LagEdge
from lagmatrix.shocks import standardised_moves

TRAIL = 60
MOVE_WIN = 3
SIGMA = 2.0


def _two_leader_fixture() -> tuple[pd.DataFrame, date]:
    """Two-leader universe: `BIGLEAD` gets a large positive engineered jump,
    `SMALLEAD` a smaller negative one, over the `MOVE_WIN` sessions ending at
    `as_of` -- same construction as `test_market_scan.py`'s `_shock_fixture`.
    Differing in both sign and magnitude means a bug that stores `abs(z)`, or
    that copies one leader's z onto the other's candidate, cannot hide behind
    a same-sign or same-magnitude fixture.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(11)
    data = {}
    for col, jump in [("BIGLEAD", 0.30), ("SMALLEAD", -0.10)]:
        r = rng.normal(0, 0.001, n)
        r[TRAIL : TRAIL + MOVE_WIN] += jump
        data[col] = 100 * np.exp(np.cumsum(r))
    closes = pd.DataFrame(data, index=idx)
    as_of = idx[TRAIL + MOVE_WIN - 1].date()
    return closes, as_of


def _lag_edge(leader: str, lagger: str) -> LagEdge:
    """Filler `LagEdge` -- these tests exercise `origin_sigma` propagation,
    not the correlation/beta fields, which are never inspected below.
    """
    return LagEdge(
        leader=leader, lagger=lagger, correlation=0.4, lag_days=1, beta=0.2, relation="supplier"
    )


class _FakeTopology:
    """Minimal fake `ArangoTopology`-shaped adapter -- returns canned
    `LagEdge`s from `laggers_of`, ignoring `max_hops`/`as_of` (this file does
    not test point-in-time filtering; `test_market_scan.py` already does).
    """

    def __init__(self, edges_by_leader: dict[str, list[LagEdge]]):
        self._edges_by_leader = edges_by_leader

    def laggers_of(self, leader: str, max_hops: int, as_of: date) -> list[LagEdge]:
        return self._edges_by_leader.get(leader, [])


def test_candidate_origin_sigma_defaults_to_none():
    """Every existing `Candidate(...)` construction site omits `origin_sigma`
    and must keep working.

    Falsifies if: `Candidate` has no `origin_sigma` field at all (today's
    state -- accessing it raises `AttributeError`), or the default is
    anything other than `None`.
    """
    cand = Candidate(symbol="AAA", direction="up", as_of=date(2026, 1, 1), origin="external")

    assert cand.origin_sigma is None


def test_market_scan_populates_origin_sigma_with_the_claiming_leaders_signed_z():
    """`MarketScan.candidates()` must carry each candidate's claiming
    leader's own signed z (`adapters/candidates.py:123`'s `z`, computed and
    currently discarded), not its absolute value and not the other leader's
    value.

    Falsifies if: `origin_sigma` is `None`, is `abs(z)` instead of the signed
    `z`, or either candidate carries the other leader's z instead of its own
    claiming leader's.
    """
    closes, as_of = _two_leader_fixture()
    fake = _FakeTopology(
        {
            "BIGLEAD": [_lag_edge("BIGLEAD", "BIGLAG")],
            "SMALLEAD": [_lag_edge("SMALLEAD", "SMALLAG")],
        }
    )
    scan = MarketScan(closes, fake, trail=TRAIL, move_win=MOVE_WIN, sigma=SIGMA)

    result = {c.symbol: c for c in scan.candidates(as_of)}

    returns = closes.pct_change()
    baseline = returns.iloc[0:TRAIL]
    recent = returns.iloc[TRAIL : TRAIL + MOVE_WIN]
    expected = standardised_moves(recent, ["BIGLEAD", "SMALLEAD"], MOVE_WIN, baseline)

    assert result["BIGLAG"].origin_sigma == pytest.approx(float(expected["BIGLEAD"]))
    assert result["SMALLAG"].origin_sigma == pytest.approx(float(expected["SMALLEAD"]))
    assert result["BIGLAG"].origin_sigma > 0
    assert result["SMALLAG"].origin_sigma < 0
    assert result["BIGLAG"].origin_sigma != result["SMALLAG"].origin_sigma


def test_external_signals_leaves_origin_sigma_none(tmp_path):
    """Alert-fed candidates have no shock z to report -- `ExternalSignals`
    must leave `origin_sigma` at its `None` default, never inherit a stray
    value through the shared `Candidate` constructor.

    Falsifies if: `origin_sigma` is anything other than `None` for an
    `ExternalSignals`-sourced candidate.
    """
    path = tmp_path / "fires.csv"
    with path.open("w") as fh:
        fh.write("ticker,posted_at,direction\n")
        fh.write("SYNA,2026-05-29,up\n")

    [cand] = ExternalSignals(path).candidates()

    assert cand.origin_sigma is None
