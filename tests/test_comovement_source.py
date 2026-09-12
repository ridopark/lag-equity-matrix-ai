"""RED for `lagmatrix.adapters.candidates.CoMovementFollowers`: turns a
shocked leader into its co-movement followers (D-95), so the existing
LangGraph pipeline can run on scan mode's other seam -- the same
`CandidateSource` protocol `MarketScan` already fills (D-23), so no node in
the graph branches on which one produced its input.

Design choices pinned here, since the class does not exist yet to pin them
itself:

* **Direction is the leader's own shock sign, inherited or flipped by the
  edge's sign.** A follower whose co-movement is positively correlated with
  the leader goes the same way the leader shocked; a negatively correlated
  one goes the opposite way. This is the one piece of real logic
  `CoMovementFollowers` has, so both signs get their own fixture below, not
  just the positive case -- `test_direction_is_opposite_leader_for_a_
  negatively_correlated_follower` is the disconfirming one.
* **"Did the leader move" reuses `MarketScan`'s own shocked-leader
  contract**, per the assignment: an unshocked leader yields `[]`, exactly
  like `MarketScan.candidates` does for a leader that never clears its
  sigma threshold (`test_market_scan.py`'s
  `test_candidates_empty_when_nothing_shocked`). `TRAIL`/`MOVE_WIN`/`SIGMA`
  below are copied verbatim from that file's constants for this reason, not
  guessed.
* **The correlation window ends strictly before `as_of`** (D-16), same as
  `comovement_edges` itself -- this file's own lookahead guard mirrors
  `test_comovement.py`'s `..._excludes_the_as_of_sessions_own_return`.
* **No forward-looking field.** D-93/D-94 killed lagged prediction; D-95 is
  contemporaneous-only. `Candidate` is the one model shared by every
  `CandidateSource` (D-23) and must not grow a field that reads as a
  forecast for this mode alone.

Fixture columns are inserted in the order `LEAD, ZZZ, AAA, UNCORR` --
deliberately not alphabetical and not correlation-magnitude order -- so a
result that merely reflects `comovement_edges`'s own internal discovery
order (or an unsorted correlation-magnitude order) cannot coincidentally
pass the sort and leader-exclusion assertions below. `AAA` sits before
`LEAD` alphabetically and `ZZZ` after it, so the leader-exclusion guard
covers both edge orientations `comovement_edges` can hand back (D-23's Q-26
class of bug: silently taking the wrong side of a pair).

Every correlation this file asserts on is computed directly with
pandas/numpy in the test body, matching `test_comovement.py`'s and
`test_market_scan.py`'s own house rule -- never a hand-guessed constant.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from conftest import SESSION_OFFSETS
from lagmatrix.adapters.candidates import CoMovementFollowers
from lagmatrix.domain.models import Candidate

TRAIL = 60
MOVE_WIN = 3
SIGMA = 2.0


def _window_corr(closes: pd.DataFrame, as_of, trail: int) -> pd.DataFrame:
    """The exact correlation matrix `comovement_edges` would compute: the
    `trail` sessions strictly before `as_of`. Used only to assert the
    fixture actually engineers the relationship each test needs -- never to
    guess a constant.
    """
    session_dates = closes.index.date
    ti = int(np.where(session_dates == as_of)[0][0])
    window = closes.pct_change().iloc[ti - trail : ti]
    return window.corr()


def _leader_followers_fixture(rng_seed: int = 0) -> tuple[pd.DataFrame, date]:
    """`LEAD` gets the same engineered +0.20 jump as `test_market_scan.py`'s
    `_shock_fixture`, over the `MOVE_WIN` sessions ending exactly at
    `as_of`, so `MarketScan`'s own shocked-leader contract fires on it
    unmodified. `AAA` tracks `LEAD`'s own daily returns closely (`0.9 *
    LEAD` plus small independent noise) -- a positive-correlation follower,
    alphabetically *before* `LEAD`. `ZZZ` is the mirror, `-0.9 * LEAD` --
    negative correlation, alphabetically *after* `LEAD`. `UNCORR` is
    independent noise, uncorrelated with `LEAD`, and must never surface.

    Sized `TRAIL + MOVE_WIN + 5` sessions, `as_of` at `TRAIL + MOVE_WIN - 1`
    -- identical layout to `_shock_fixture`; the trailing 5 sessions are
    pure buffer so a "later" session always exists for `MarketScan`'s own
    point-in-time idiom.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(rng_seed)
    r_lead = rng.normal(0, 0.001, n)
    r_lead[TRAIL : TRAIL + MOVE_WIN] += 0.20
    r_aaa = 0.9 * r_lead + rng.normal(0, 0.0002, n)
    r_zzz = -0.9 * r_lead + rng.normal(0, 0.0002, n)
    r_uncorr = rng.normal(0, 0.001, n)
    closes = pd.DataFrame(
        {
            "LEAD": 100 * np.exp(np.cumsum(r_lead)),
            "ZZZ": 100 * np.exp(np.cumsum(r_zzz)),
            "AAA": 100 * np.exp(np.cumsum(r_aaa)),
            "UNCORR": 100 * np.exp(np.cumsum(r_uncorr)),
        },
        index=idx,
    )
    as_of = idx[TRAIL + MOVE_WIN - 1].date()
    return closes, as_of


def _unshocked_leader_fixture() -> tuple[pd.DataFrame, date]:
    """Same shape as `_leader_followers_fixture`, but `LEAD` never jumps --
    pure noise throughout -- while `AAA` still tracks it closely. Used to
    prove an unmoved leader yields `[]` even though a strongly correlated
    follower exists to discover.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(1)
    r_lead = rng.normal(0, 0.001, n)
    r_aaa = 0.9 * r_lead + rng.normal(0, 0.0002, n)
    closes = pd.DataFrame(
        {
            "LEAD": 100 * np.exp(np.cumsum(r_lead)),
            "AAA": 100 * np.exp(np.cumsum(r_aaa)),
        },
        index=idx,
    )
    as_of = idx[TRAIL + MOVE_WIN - 1].date()
    return closes, as_of


def test_direction_matches_leader_for_a_positively_correlated_follower():
    """Falsifies if: `AAA` is missing from the result, its `direction` is
    not `"up"` (the same sign `LEAD` shocked), `origin` is not
    `"comovement"`, or `origin_leader` is not `"LEAD"`.
    """
    closes, as_of = _leader_followers_fixture()
    corr = _window_corr(closes, as_of, TRAIL)
    assert corr.loc["LEAD", "AAA"] > 0.5, "fixture must engineer a real positive correlation"

    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)
    result = source.candidates(as_of)

    assert result, "fixture must produce at least one candidate for this test to mean anything"
    by_symbol = {c.symbol: c for c in result}
    assert "AAA" in by_symbol
    assert by_symbol["AAA"].direction == "up"
    assert by_symbol["AAA"].origin == "comovement"
    assert by_symbol["AAA"].origin_leader == "LEAD"


def test_direction_is_opposite_leader_for_a_negatively_correlated_follower():
    """The disconfirming case: `ZZZ` anti-correlates with `LEAD`, so it must
    inherit the *opposite* of the leader's own "up" shock.

    Falsifies if: `ZZZ`'s `direction` is `"up"` (i.e. the implementation
    ignored the edge's sign and always copied the leader's own direction
    verbatim) or `ZZZ` is missing entirely.
    """
    closes, as_of = _leader_followers_fixture()
    corr = _window_corr(closes, as_of, TRAIL)
    assert corr.loc["LEAD", "ZZZ"] < -0.5, "fixture must engineer a real negative correlation"

    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)
    result = source.candidates(as_of)

    assert result, "fixture must produce at least one candidate for this test to mean anything"
    by_symbol = {c.symbol: c for c in result}
    assert "ZZZ" in by_symbol
    assert by_symbol["ZZZ"].direction == "down"


def test_uncorrelated_symbol_never_becomes_a_candidate():
    """Falsifies if: `UNCORR` appears in the result despite not clearing
    `min_abs_corr` against `LEAD`.
    """
    closes, as_of = _leader_followers_fixture()
    corr = _window_corr(closes, as_of, TRAIL)
    assert abs(corr.loc["LEAD", "UNCORR"]) < 0.3, "fixture must keep UNCORR genuinely uncorrelated"

    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)
    result = source.candidates(as_of)

    assert result, "fixture must still produce other candidates for this test to mean anything"
    assert "UNCORR" not in {c.symbol for c in result}


def test_leader_is_never_among_its_own_candidates():
    """`AAA` sits alphabetically before `LEAD`, `ZZZ` after it -- so
    `comovement_edges` hands back the `LEAD`-touching pair as `(AAA, LEAD)`
    for one and `(LEAD, ZZZ)` for the other. An implementation that always
    took, say, `edge.b` as "the follower" without checking which side is
    actually the leader would emit `LEAD` itself in place of `AAA`.

    Falsifies if: `"LEAD"` appears anywhere in the result's symbols.
    """
    closes, as_of = _leader_followers_fixture()
    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)

    result = source.candidates(as_of)

    assert result, "fixture must produce candidates for this test to mean anything"
    assert "LEAD" not in {c.symbol for c in result}


def test_excluded_symbols_are_dropped_even_when_strongly_correlated():
    """Falsifies if: `AAA` leaks into the result despite being named in
    `excluded_symbols`, while `ZZZ` (not excluded) is still correctly
    present.
    """
    closes, as_of = _leader_followers_fixture()

    source = CoMovementFollowers(
        closes, "LEAD", trail=TRAIL, min_abs_corr=0.3, excluded_symbols=frozenset({"AAA"})
    )
    result = source.candidates(as_of)

    symbols = {c.symbol for c in result}
    assert "AAA" not in symbols
    assert "ZZZ" in symbols


def test_candidates_sorted_by_symbol_ascending():
    """The fixture's columns are inserted `LEAD, ZZZ, AAA, UNCORR` -- the
    opposite of alphabetical -- so a result that merely preserves
    `comovement_edges`'s own discovery order would read `["ZZZ", "AAA"]`.

    Falsifies if: the output is not exactly `["AAA", "ZZZ"]` in that order.
    """
    closes, as_of = _leader_followers_fixture()
    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)

    result = source.candidates(as_of)

    assert [c.symbol for c in result] == ["AAA", "ZZZ"]


def test_unshocked_leader_returns_empty_list():
    """`LEAD` never jumps in this fixture, so it can never appear in
    `MarketScan`'s own shocked-leader contract -- `CoMovementFollowers` must
    match that contract exactly, even though `AAA` correlates strongly
    enough to otherwise qualify.

    Falsifies if: this returns anything other than `[]`.
    """
    closes, as_of = _unshocked_leader_fixture()
    corr = _window_corr(closes, as_of, TRAIL)
    assert corr.loc["LEAD", "AAA"] > 0.5, "fixture must still engineer a real correlation"

    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)
    result = source.candidates(as_of)

    assert result == []


def test_insufficient_history_returns_empty_list_not_a_crash():
    """`as_of` sits far too early for either `MarketScan`'s shock window or
    `comovement_edges`'s correlation window to have enough trailing
    sessions.

    Falsifies if: this raises (e.g. a negative `.iloc` slice silently
    wrapping around) instead of returning `[]`.
    """
    closes, _ = _leader_followers_fixture()
    as_of = closes.index[1].date()
    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)

    assert source.candidates(as_of) == []


def test_correlation_window_excludes_the_as_of_sessions_own_return():
    """D-16, mirroring `test_comovement.py`'s own guard: the correlation
    edge that decides whether `AAA` is a candidate, and what direction it
    gets, must not depend on either symbol's return on `as_of` itself --
    only on the trail strictly before it. Two variants share identical data
    for every session before `as_of` and differ only in `AAA`'s own as_of-
    session return (a deliberately huge, distinctive outlier in the
    "shocked" variant).

    Falsifies if: the candidate set or any direction differs between the
    two variants -- i.e. `CoMovementFollowers` (or the correlation window it
    builds) reached through `as_of` instead of stopping strictly before it.
    """
    n = TRAIL + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(2)
    r_lead = rng.normal(0, 0.001, n)
    r_lead[TRAIL : TRAIL + MOVE_WIN] += 0.20
    r_aaa_base = 0.9 * r_lead + rng.normal(0, 0.0002, n)
    as_of_row = TRAIL + MOVE_WIN - 1
    as_of = idx[as_of_row].date()

    def _closes(aaa_as_of_return: float) -> pd.DataFrame:
        r_aaa = r_aaa_base.copy()
        r_aaa[as_of_row] = aaa_as_of_return
        return pd.DataFrame(
            {
                "LEAD": 100 * np.exp(np.cumsum(r_lead)),
                "AAA": 100 * np.exp(np.cumsum(r_aaa)),
            },
            index=idx,
        )

    quiet = _closes(aaa_as_of_return=r_aaa_base[as_of_row])
    shocked = _closes(aaa_as_of_return=-0.9)  # a 90% single-session drop, only on as_of itself

    result_quiet = CoMovementFollowers(quiet, "LEAD", trail=TRAIL, min_abs_corr=0.3).candidates(
        as_of
    )
    result_shocked = CoMovementFollowers(
        shocked, "LEAD", trail=TRAIL, min_abs_corr=0.3
    ).candidates(as_of)

    assert result_quiet, "fixture must produce a candidate for this test to mean anything"
    assert [(c.symbol, c.direction) for c in result_quiet] == [
        (c.symbol, c.direction) for c in result_shocked
    ]


def test_candidates_are_plain_candidates_with_no_forward_looking_field():
    """D-93/D-94 killed lagged prediction; D-95 is contemporaneous-only.
    `Candidate` is the one model shared by every `CandidateSource` (D-23),
    so this checks the exact field set rather than mere equality --
    pydantic's `extra="ignore"` would silently drop an undeclared kwarg like
    `expected_move=` on both sides of an `==` comparison, letting such a
    field slip in unnoticed (the same trap `test_market_scan.py`'s
    `origin_leader` tests document).

    Falsifies if: any emitted object is not exactly a `Candidate` (e.g. a
    subclass smuggling extra data), or its field set differs from
    `Candidate`'s current declared set -- in particular if a forward-looking
    field such as an expected move or a score were added for this mode.

    `origin_sigma` was added 2026-09-11 (D-130) and this guard correctly
    fired on it. It is admitted deliberately, not waved through: it is the
    claiming leader's **signed z over the window ending at `as_of`** -- a
    move that has already happened -- not an expectation, a forecast or a
    score. `direction` is already derived from that same z's sign and has
    always been in this set, so the model demonstrably already carried the
    quantity; this adds its magnitude. A field naming something that has not
    happened yet still falsifies this test, which is the point.
    """
    closes, as_of = _leader_followers_fixture()
    source = CoMovementFollowers(closes, "LEAD", trail=TRAIL, min_abs_corr=0.3)

    result = source.candidates(as_of)

    assert result, "fixture must produce at least one candidate for this test to mean anything"
    for c in result:
        assert type(c) is Candidate
        assert set(c.model_dump().keys()) == {
            "symbol",
            "direction",
            "as_of",
            "as_of_ts",
            "origin",
            "origin_leader",
            "origin_sigma",
        }


# --- Q-66: `_leader_shock_z`'s `ti` resolution must not depend on the
# index's time of day, and must include the as-of session itself ----------
#
# `_leader_shock_z` reuses `MarketScan.shocked_leaders`'s own `ti` idiom
# (`sessions.get_loc(sessions[sessions > str(as_of)][0])`), so it inherits
# the same bug: `str(as_of)` compares against midnight, so a midnight index
# includes the as-of session in the `recent` window and a 04:00 index (like
# both production price files) excludes it. Per
# `docs/plans/PLAN-2026-09-08-market-scan.md:183-192`, inclusion is the
# deliberate, correct contract. Tested through `candidates(as_of)`, the
# public seam -- `_leader_shock_z` is a private implementation detail of it.


def _single_day_jump_leader_fixture(rng_seed: int = 0) -> tuple[pd.DataFrame, date]:
    """`LEAD` is quiet noise on every session except one unmistakable move
    (log-return 0.20, vs ~0.001 baseline noise) confined to the as-of
    session alone. `AAA` tracks `LEAD` closely (`0.9 * LEAD` plus small
    independent noise) on every session -- including the as-of session,
    but `comovement_edges` only ever measures the `trail` sessions strictly
    before `as_of` (D-16), so AAA's measured correlation with LEAD is
    unaffected by the as-of day's jump either way; only whether `LEAD`
    itself is judged "shocked" depends on it.

    A `buffer` of 10 quiet sessions sits between the `TRAIL`-session
    baseline and `as_of`, same reason as `_single_day_jump_fixture` in
    `test_market_scan.py`: shifting `ti` by one session must land on an
    ordinary quiet session (a suppressed z), not trip the `ti < move_win +
    trail` guard (an early return) -- the former is the sharper, more
    representative failure.
    """
    buffer = 10
    n = TRAIL + buffer + MOVE_WIN + 5
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    rng = np.random.default_rng(rng_seed)
    r_lead = rng.normal(0, 0.001, n)
    as_of_pos = TRAIL + buffer + MOVE_WIN - 1
    r_lead[as_of_pos] += 0.20
    r_aaa = 0.9 * r_lead + rng.normal(0, 0.0002, n)
    closes = pd.DataFrame(
        {"LEAD": 100 * np.exp(np.cumsum(r_lead)), "AAA": 100 * np.exp(np.cumsum(r_aaa))},
        index=idx,
    )
    as_of = idx[as_of_pos].date()
    return closes, as_of


def _candidate_symbols(base_closes: pd.DataFrame, as_of: date, offset: pd.Timedelta) -> set[str]:
    shifted = base_closes.copy()
    shifted.index = base_closes.index + offset
    source = CoMovementFollowers(shifted, "LEAD", trail=TRAIL, min_abs_corr=0.3)
    return {c.symbol for c in source.candidates(as_of)}


def test_candidates_is_time_of_day_independent():
    """Same `as_of`, same returns, two index shapes -- `candidates` must
    agree.

    Falsifies if: the returned symbol set differs between the
    midnight-indexed and 04:00-indexed runs. Measured today: `{"AAA"}`
    (midnight) vs `set()` (04:00, LEAD's own shock z is -0.89 there and
    never clears `sigma=2.0`, so `_leader_shock_z` returns `None` and
    `candidates` short-circuits to `[]` before ever calling
    `comovement_edges`).
    """
    base_closes, as_of = _single_day_jump_leader_fixture()
    midnight = _candidate_symbols(base_closes, as_of, pd.Timedelta(0))
    prod_0400 = _candidate_symbols(base_closes, as_of, pd.Timedelta(hours=4))
    assert midnight == prod_0400


def test_candidates_detects_a_leader_shock_confined_to_the_as_of_session():
    """LEAD's only move is on the as-of session, so `candidates` finding
    its follower `AAA` at all is only possible when that session's return
    is folded into `_leader_shock_z`'s window -- on both index shapes,
    since the as-of session is deliberately included by contract, not an
    artefact of one index shape.

    Falsifies if: `AAA` is missing from `candidates(as_of)` on either
    offset. Measured: LEAD z=130.9 (midnight, clears `sigma=2.0`, AAA
    present) vs z=-0.89 (04:00, does not clear threshold -- `candidates`
    returns `[]`, AAA absent).
    """
    base_closes, as_of = _single_day_jump_leader_fixture()
    for offset in SESSION_OFFSETS.values():
        symbols = _candidate_symbols(base_closes, as_of, offset)
        assert "AAA" in symbols, f"offset={offset}: AAA missing from candidates"
