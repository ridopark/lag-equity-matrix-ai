"""Q-37: `runner.run` breaks on a `MarketScan`-shaped `signals=` source.

`runner.py` has three defects around its two `signals.candidates()` call
sites (lines 74 and 80):

1. Line 80 calls `signals.candidates()` with no `as_of` at all.
   `ExternalSignals.candidates` defaults `as_of` to `None` and tolerates it;
   `MarketScan.candidates(self, as_of: date)` has no default, so passing a
   `MarketScan` raises `TypeError` there -- after line 74 already succeeded.
2. It computes candidates twice for one run: once (filtered) for the
   candidate list, once again (unfiltered) for `signal_universe`. For
   `MarketScan` a "call" is a full sweep of the universe plus a round of
   graph traversals, not a cheap re-read.
3. It has the leader re-entry hole `serve.py` closed for scan mode
   (D-23's Outcome; mechanism proved end-to-end at the graph level by
   `test_market_scan.py::test_unioning_the_originating_leader_into_signal_
   universe_closes_the_reentry_hole`): the shocked leader that *originates*
   a scan candidate is not excluded from that candidate's own correlation
   neighbourhood, so `context_fusion` can count the leader's own move as
   evidence for the candidate it produced.

**Resolution chosen for defect 3: union, not refusal.** `MarketScan` is
already implemented, tested, and wired into the live UI (D-23's Outcome) --
refusing it in the batch runner would make the runner strictly less capable
than `serve.py` for no safety gained, since the union mechanism it would be
refusing in favour of already exists and is proven. The runner should detect
`shocked_leaders` the same way `serve.py` does (duck-typed `hasattr`, not a
`CandidateSource` protocol member) since `ExternalSignals` -- the default,
and the only source the protocol formally supports -- has no such method,
and adding it to the protocol would force every `CandidateSource` to expose
scan-only machinery it cannot implement.

These tests exercise `runner.run` directly (not `MarketScan` itself, which
`test_market_scan.py` already covers) with fakes duck-typed to `MarketScan`'s
actual shape, so each defect is isolated and none is assumed fixed by the
others.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from lagmatrix.domain.models import Candidate
from lagmatrix.pipeline import runner as runner_mod

AS_OF = date(2026, 3, 26)


class _StrictScanSource:
    """`candidates` takes `as_of` positionally with **no default** -- the
    actual shape of `MarketScan.candidates(self, as_of: date)`, unlike
    `ExternalSignals.candidates`, which defaults it to `None`. Deliberately
    has no `shocked_leaders`, so this fake isolates defect 1 only.
    """

    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates

    def candidates(self, as_of: date) -> list[Candidate]:
        return self._candidates


class _CountingScanSource:
    """Tolerates being called with no `as_of` (so a double-call bug shows up
    as an extra recorded call rather than a `TypeError` masking it), and
    exposes `shocked_leaders` -- the attribute `ExternalSignals` lacks and
    that a duck-typed runner fix must detect.
    """

    def __init__(self, candidates: list[Candidate], leaders: dict[str, float] | None = None):
        self._candidates = candidates
        self._leaders = leaders or {}
        self.calls: list[date | None] = []

    def candidates(self, as_of: date | None = None) -> list[Candidate]:
        self.calls.append(as_of)
        return self._candidates

    def shocked_leaders(self, as_of: date | None = None) -> dict[str, float]:
        return self._leaders


def _reentry_closes() -> tuple[pd.DataFrame, date]:
    """Two-column universe reproducing the leader re-entry hole, built the
    same way as `test_market_scan.py`'s `_reentry_fixture`: `Y` gets an
    engineered +0.20 jump over the `MOVE_WIN` sessions ending at `as_of`
    (>= 2 sigma against `TRAIL` sessions of baseline), and `X` is a
    deterministic linear function of `Y`'s returns (`0.8 * Y` plus small
    independent noise) -- not an independent draw that happens to
    correlate. With only two columns and `graph_retriever.py` always
    dropping the candidate's own symbol (Q-26), `Y` is `X`'s only possible
    neighbour, so whether `Y` ends up excluded is provable, not merely
    likely.
    """
    trail, move_win = 60, 3
    n = trail + move_win + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(7)
    ry = rng.normal(0, 0.001, n)
    ry[trail : trail + move_win] += 0.20
    rx = 0.8 * ry + rng.normal(0, 0.0002, n)
    closes = pd.DataFrame(
        {"Y": 100 * np.exp(np.cumsum(ry)), "X": 100 * np.exp(np.cumsum(rx))}, index=idx
    )
    as_of = idx[trail + move_win - 1].date()
    return closes, as_of


def _patch_checkpoint_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner_mod, "CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite"))


async def test_run_does_not_raise_typeerror_for_marketscan_shaped_candidates_signature(
    closes, monkeypatch, tmp_path
):
    """Defect 1: `run()` must not call `signals.candidates()` with no
    `as_of` when the injected source requires one.

    Falsifies if: this raises `TypeError` (today it does, at `runner.py:80`
    -- `TypeError: candidates() missing 1 required positional argument:
    'as_of'`) instead of returning an assessments list.
    """
    _patch_checkpoint_db(monkeypatch, tmp_path)
    cand = Candidate(symbol="CAND", direction="up", as_of=AS_OF, origin="scan")
    fake = _StrictScanSource([cand])

    assessments, _thread_id, _interrupt = await runner_mod.run(
        AS_OF, closes=closes, with_news=False, signals=fake,
    )

    assert isinstance(assessments, list)


async def test_run_calls_candidates_exactly_once_per_run(closes, monkeypatch, tmp_path):
    """Defect 2: `signal_universe` must be built from the candidate list
    already fetched for `as_of`, not from a second, unfiltered call to
    `signals.candidates()`. For `MarketScan` a second call is a second full
    sweep of the universe plus a second round of graph traversals for a
    result already in hand.

    Uses a source that tolerates a call with no `as_of`, so a double-call
    shows up as an extra recorded call rather than as a `TypeError`
    (defect 1's concern, tested separately above).

    Falsifies if: `fake.calls` has more than one entry -- today it is
    exactly `[AS_OF, None]`.
    """
    _patch_checkpoint_db(monkeypatch, tmp_path)
    cand = Candidate(symbol="CAND", direction="up", as_of=AS_OF, origin="scan")
    fake = _CountingScanSource([cand])

    await runner_mod.run(AS_OF, closes=closes, with_news=False, signals=fake)

    assert fake.calls == [AS_OF], (
        f"expected exactly one call to candidates(), with as_of, got {fake.calls}"
    )


async def test_run_excludes_originating_leader_from_its_own_candidates_neighbourhood(
    monkeypatch, tmp_path
):
    """Defect 3: the shocked leader that produced a scan candidate must be
    excluded from that candidate's own correlation neighbourhood, the same
    way `serve.py` unions `MarketScan.shocked_leaders()` into
    `signal_universe` (D-23's Outcome).

    `_reentry_closes()` engineers `X`'s only possible neighbour to be `Y`,
    the very leader that the fake source reports as having produced `X`
    (`shocked_leaders` returns `{"Y": ...}`). With `Y` correctly excluded,
    `X`'s correlation pool is empty (its only column and itself both
    dropped), `route_on_neighbourhood` sends the branch straight to `END`
    (D-27's no-neighbourhood path), and `run()` produces no assessment for
    `X` at all -- exactly the graph-level outcome
    `test_unioning_the_originating_leader_into_signal_universe_closes_the_
    reentry_hole` proves for `signal_universe={"Y"}` directly.

    Falsifies if: `assessments` is non-empty -- today it is, because the
    runner never calls `shocked_leaders()` at all, so `Y` stays out of
    `signal_universe`, lands in `X`'s top-k neighbour pool by construction,
    and `context_fusion` counts `Y`'s own engineered shock as `leader_move`
    evidence for `X`.
    """
    _patch_checkpoint_db(monkeypatch, tmp_path)
    closes, as_of = _reentry_closes()
    cand = Candidate(symbol="X", direction="up", as_of=as_of, origin="scan")
    fake = _CountingScanSource([cand], leaders={"Y": 5.0})

    assessments, _thread_id, _interrupt = await runner_mod.run(
        as_of, closes=closes, with_news=False, signals=fake,
    )

    assert assessments == [], (
        "expected no assessment for X once its originating leader Y is "
        f"excluded from its neighbourhood, got {assessments}"
    )
