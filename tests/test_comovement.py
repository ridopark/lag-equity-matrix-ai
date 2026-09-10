"""RED for `lagmatrix.comovement` (D-95): the graph is built from measured
contemporaneous co-movement, not lagged prediction. Every edge carries a
correlation AND a calibrated confidence interval on how precisely that
correlation is measured -- never a claim about what one name does after the
other (D-95's own caution).

Design choices pinned here, since the module does not exist yet to pin them
itself:

* **Returns, not "market-excess" returns.** D-95's calibration table was run
  through D-93's `standardise()` (market-excess demean, then z-score), but
  `comovement_edges`'s own spec example ("two correlated columns, one
  independent, the independent one does not clear `min_abs_corr`") is only
  satisfiable with plain `pct_change()` correlation: verified directly by
  demeaning that exact 3-column fixture and finding the "independent" column
  spuriously anti-correlates at -0.94 with both of the others -- a pure
  artefact of a tiny pool, not a real relationship. Plain `pct_change()` also
  matches `graph_retriever.retrieve_neighbourhood`'s existing correlation-edge
  convention. Tests below use plain returns throughout.
* **Point-in-time cutoff.** The window is the `trail` sessions strictly
  before `as_of` -- i.e. `as_of`'s own session return is excluded, not just
  sessions after it. This is stricter than `graph_retriever.py`'s existing
  `ti = position of the first session AFTER as_of` (whose window's last row
  IS the as_of session's own return); `comovement_edges`'s spec says "ending
  strictly before as_of", so `ti` here is the position of `as_of` itself and
  the window is `returns.iloc[ti-trail:ti]`. `test_comovement_edges_excludes_
  the_as_of_sessions_own_return` is written to fail if an implementation
  copies the older off-by-one instead.
* **95% Fisher z-transform interval**, `math.atanh`/`math.tanh`, `z* = 1.96`,
  `se = 1/sqrt(n-3)` -- the standard construction; not specified
  by name elsewhere in this repo, so pinned explicitly here.

Every correlation this file asserts on is computed with numpy/pandas in the
test body itself, never a hand-guessed constant (house rule from
`test_nodes.py`'s docstrings: "computed directly against this fixture before
writing this assertion").
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from lagmatrix.comovement import comovement_edges, confidence_interval, duplicate_flag
from lagmatrix.domain.models import ComovementEdge  # noqa: F401 -- exercised via comovement_edges

# --- comovement_edges -------------------------------------------------------


def _two_bloc_closes(n: int = 60, extra_sessions: int = 1) -> tuple[pd.DataFrame, date, dict]:
    """Four columns, two independent 2-symbol blocs: A-B correlated, C-D
    correlated, every cross-bloc pair uncorrelated. Verified directly with
    numpy before writing any assertion (`seed=0`, `n=60`):

        corr(A, B) =  0.932   corr(C, D) =  0.938
        corr(A, C) = -0.040   corr(A, D) =  0.025
        corr(B, C) = -0.043   corr(B, D) =  0.009

    Two independent qualifying pairs, rather than one pair plus one inert
    column, so tests that remove or count edges (exclude, symmetry) have a
    positive contrast to check against -- a function that always returned
    `[]` would still pass a test built on a single pair.

    `n` sessions of engineered returns sit in rows `1..n` (row 0 is the usual
    throwaway base row, its `pct_change` always NaN); `as_of` is the session
    at row `n + 1`, so the `trail=n` window (`iloc[1:n+1]`) covers exactly the
    engineered rows and excludes `as_of`'s own row. `extra_sessions` appends
    that many further rows of arbitrary data after `as_of`, for tests that
    need a "future" to prove is ignored.

    Returns `(closes, as_of, expected)`, where `expected` is the dict of
    engineered return arrays actually used, keyed by symbol, so a caller can
    recompute any correlation directly from the same numbers.
    """
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.01, n)
    b = 0.9 * a + rng.normal(0, 0.003, n)
    c = rng.normal(0, 0.01, n)
    d = 0.9 * c + rng.normal(0, 0.003, n)

    total = n + 2 + extra_sessions  # base row + n window rows + as_of row + future rows
    idx = pd.bdate_range("2026-01-01", periods=total, tz="UTC")
    as_of = idx[n + 1].date()

    zeros = np.zeros(1 + extra_sessions)  # as_of's own row + any future rows: value is irrelevant
    r = {
        "A": np.concatenate([[0.0], a, zeros]), "B": np.concatenate([[0.0], b, zeros]),
        "C": np.concatenate([[0.0], c, zeros]), "D": np.concatenate([[0.0], d, zeros]),
    }
    closes = pd.DataFrame(
        {k: 100 * np.cumprod(1 + v) for k, v in r.items()}, index=idx
    )
    return closes, as_of, {"A": a, "B": b, "C": c, "D": d}


def test_comovement_edges_keeps_correlated_pair_and_drops_uncorrelated_one():
    """Checklist case 1: A-B is engineered to correlate, A-C/A-D/B-C/B-D are
    not. The measured correlation must match a plain-`pct_change` numpy
    computation over the same window, not a guessed constant.

    Falsifies if: the A-B edge is missing, its `corr` disagrees with the
    directly-computed numpy value, or an edge for any cross-bloc pair
    (A-C, A-D, B-C, B-D) is present -- any of those would mean the threshold
    or the returns computation is wrong.
    """
    closes, as_of, expected = _two_bloc_closes()
    expected_ab = float(np.corrcoef(expected["A"], expected["B"])[0, 1])
    assert abs(expected_ab) > 0.5, "fixture must engineer a real correlation to test against"

    edges = comovement_edges(closes, as_of, trail=60, min_abs_corr=0.3)

    assert edges, "fixture must produce at least one edge for this test to mean anything"
    pairs = {frozenset((e.a, e.b)): e for e in edges}
    assert frozenset(("A", "B")) in pairs
    ab = pairs[frozenset(("A", "B"))]
    assert ab.corr == pytest.approx(expected_ab, abs=1e-9)
    assert ab.n_sessions == 60
    for bad_pair in [("A", "C"), ("A", "D"), ("B", "C"), ("B", "D")]:
        assert frozenset(bad_pair) not in pairs


def test_comovement_edges_keeps_negative_correlation_above_threshold():
    """The threshold is on |corr|, not corr: an inverse pair at ~-0.9 must
    still surface at `min_abs_corr=0.3`, with its sign preserved.

    Falsifies if: no edge is returned for the pair (an implementation
    comparing `corr >= min_abs_corr` instead of `abs(corr) >= min_abs_corr`
    would drop every negative pair), or `corr` is positive.
    """
    n = 60
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.01, n)
    y = -0.85 * x + rng.normal(0, 0.004, n)
    expected_xy = float(np.corrcoef(x, y)[0, 1])
    assert expected_xy < -0.5, "fixture must engineer a real negative correlation to test against"

    idx = pd.bdate_range("2026-01-01", periods=n + 2, tz="UTC")
    as_of = idx[n + 1].date()
    r = {"X": np.concatenate([[0.0], x, [0.0]]), "Y": np.concatenate([[0.0], y, [0.0]])}
    closes = pd.DataFrame({k: 100 * np.cumprod(1 + v) for k, v in r.items()}, index=idx)

    edges = comovement_edges(closes, as_of, trail=n, min_abs_corr=0.3)

    assert edges, (
        "fixture must produce the negative-correlation edge for this test to mean anything")
    xy = {frozenset((e.a, e.b)): e for e in edges}[frozenset(("X", "Y"))]
    assert xy.corr == pytest.approx(expected_xy, abs=1e-9)
    assert xy.corr < 0


def test_comovement_edges_exclude_removes_a_symbol_from_the_pool_entirely():
    """`exclude={"A"}` must drop every pair touching A, while leaving the
    unrelated C-D pair untouched.

    Falsifies if: an A-* edge still appears (exclude not honoured), or the
    C-D edge is gone too (exclude over-applied, or the function just returns
    `[]` regardless of input -- the C-D survivor rules that out).
    """
    closes, as_of, expected = _two_bloc_closes()
    expected_cd = float(np.corrcoef(expected["C"], expected["D"])[0, 1])

    edges = comovement_edges(closes, as_of, trail=60, min_abs_corr=0.3, exclude=frozenset({"A"}))

    assert edges, "fixture's C-D pair must still qualify with A excluded"
    symbols = {s for e in edges for s in (e.a, e.b)}
    assert "A" not in symbols
    pairs = {frozenset((e.a, e.b)): e for e in edges}
    assert frozenset(("C", "D")) in pairs
    assert pairs[frozenset(("C", "D"))].corr == pytest.approx(expected_cd, abs=1e-9)


def test_comovement_edges_insufficient_history_returns_empty_list():
    """Fewer than `trail` sessions strictly before `as_of` must yield `[]`,
    not raise. Only 3 sessions precede `as_of` here while `trail=10` is
    requested.

    Falsifies if: this raises (e.g. a negative-index `iloc` slice silently
    wrapping instead of being guarded) or returns anything other than `[]`.
    """
    idx = pd.bdate_range("2026-01-01", periods=5, tz="UTC")
    as_of = idx[3].date()
    closes = pd.DataFrame(
        {"A": [100.0, 101.0, 99.0, 100.5, 101.5], "B": [50.0, 50.5, 49.5, 50.2, 50.8]},
        index=idx,
    )

    edges = comovement_edges(closes, as_of, trail=10, min_abs_corr=0.3)

    assert edges == []


def test_comovement_edges_returns_exactly_one_edge_per_unordered_pair():
    """Two independent qualifying pairs (A-B, C-D) must produce exactly two
    edges total -- not four from also emitting each pair's reverse.

    Falsifies if: `len(edges) != 2`, or the same unordered pair appears
    twice (e.g. once as (A, B) and once as (B, A)).
    """
    closes, as_of, _ = _two_bloc_closes()

    edges = comovement_edges(closes, as_of, trail=60, min_abs_corr=0.3)

    assert edges, "fixture must produce edges for this test to mean anything"
    pair_keys = [frozenset((e.a, e.b)) for e in edges]
    assert len(pair_keys) == 2
    assert len(set(pair_keys)) == 2
    assert set(pair_keys) == {frozenset(("A", "B")), frozenset(("C", "D"))}


def test_comovement_edges_unchanged_by_sessions_that_exist_after_as_of():
    """D-16 point-in-time: a frame with extra sessions appended after `as_of`
    must produce byte-identical edges to the same frame truncated right at
    `as_of` -- the standard "no lookahead" guard, and the one D-82 records
    catching a real bug in this repo before.

    Falsifies if: any edge's `corr`, `n_sessions`, `ci_low`, `ci_high` or
    `flag` differs between the two calls, or the edge sets differ -- any of
    which would mean a session after `as_of` reached the correlation window.
    """
    closes_with_future, as_of, _ = _two_bloc_closes(extra_sessions=3)
    # as_of sits at row n+1; truncating there drops every "future" row while
    # keeping as_of's own row (still excluded from the window by
    # `comovement_edges` itself, not by this slice).
    closes_truncated = closes_with_future.iloc[: 60 + 2]
    assert len(closes_truncated) < len(closes_with_future), (
        "fixture must actually have a future to drop")

    with_future = comovement_edges(closes_with_future, as_of, trail=60, min_abs_corr=0.3)
    truncated = comovement_edges(closes_truncated, as_of, trail=60, min_abs_corr=0.3)

    assert with_future, "fixture must produce edges for this test to mean anything"
    by_pair_future = {frozenset((e.a, e.b)):
                      (e.corr, e.n_sessions, e.ci_low, e.ci_high, e.flag)
                      for e in with_future}
    by_pair_truncated = {frozenset((e.a, e.b)):
                         (e.corr, e.n_sessions, e.ci_low, e.ci_high, e.flag)
                         for e in truncated}
    assert by_pair_future == by_pair_truncated


def test_comovement_edges_excludes_the_as_of_sessions_own_return():
    """Stricter than the "no future sessions" guard above: the window must
    also exclude `as_of`'s *own* session return, not just sessions after it.

    Two variants share identical data for every session strictly before
    `as_of` and differ *only* in the as_of session's own return (a huge,
    deliberately distinctive jump in variant B). If the window correctly
    stops the session before `as_of`, both variants must produce identical
    edges. `graph_retriever.py`'s existing (and here deliberately different)
    convention -- `ti = position of the first session AFTER as_of`, window
    ending at `ti - 1` -- would instead include the as_of row and change the
    measured correlation between the two variants, which is exactly the
    off-by-one this test is written to catch.

    Falsifies if: the two variants' edges differ in any field.
    """
    n = 10
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.01, n)
    b = 0.9 * a + rng.normal(0, 0.002, n)

    idx = pd.bdate_range("2026-01-01", periods=n + 2, tz="UTC")
    as_of = idx[n + 1].date()

    def _closes(as_of_row_a: float) -> pd.DataFrame:
        r = {
            "A": np.concatenate([[0.0], a, [as_of_row_a]]), "B": np.concatenate([[0.0], b, [0.0]]),
        }
        return pd.DataFrame({k: 100 * np.cumprod(1 + v) for k, v in r.items()}, index=idx)

    variant_quiet = _closes(as_of_row_a=0.0001)
    variant_shocked = _closes(as_of_row_a=0.5)  # a 50% jump on the as_of session itself

    quiet_edges = comovement_edges(variant_quiet, as_of, trail=n, min_abs_corr=0.3)
    shocked_edges = comovement_edges(variant_shocked, as_of, trail=n, min_abs_corr=0.3)

    assert quiet_edges, "fixture must produce the A-B edge for this test to mean anything"
    quiet_ab = {frozenset((e.a, e.b)): (e.corr, e.n_sessions)
                for e in quiet_edges}[frozenset(("A", "B"))]
    shocked_ab = {frozenset((e.a, e.b)): (e.corr, e.n_sessions)
                  for e in shocked_edges}[frozenset(("A", "B"))]
    assert quiet_ab == shocked_ab


def test_comovement_edges_flags_but_keeps_duplicate_series_pairs():
    """The artefact screen from D-95 (NATL/LINE): a pair whose returns are
    identical must still be returned as an edge (deciding what to do with a
    flagged edge is the caller's job, not `comovement_edges`'s), but flagged.

    Falsifies if: the P-Q edge is missing entirely (flagged edges wrongly
    dropped), or `flag` is not `"duplicate_series"`.
    """
    n = 60
    rng = np.random.default_rng(0)
    p = rng.normal(0, 0.01, n)

    idx = pd.bdate_range("2026-01-01", periods=n + 2, tz="UTC")
    as_of = idx[n + 1].date()
    r = {"P": np.concatenate([[0.0], p, [0.0]]),
         "Q": np.concatenate([[0.0], p, [0.0]])}  # identical
    closes = pd.DataFrame({k: 100 * np.cumprod(1 + v) for k, v in r.items()}, index=idx)

    edges = comovement_edges(closes, as_of, trail=n, min_abs_corr=0.3)

    assert edges, "an identical-returns pair must still qualify by correlation (corr == 1.0)"
    pq = {frozenset((e.a, e.b)): e for e in edges}[frozenset(("P", "Q"))]
    assert pq.flag == "duplicate_series"


# --- confidence_interval -----------------------------------------------------


def test_confidence_interval_is_wider_at_smaller_n():
    """Same corr, smaller sample: the interval must be wider -- less
    precisely measured, the honest reading of a "confidence number".

    Falsifies if: the n=20 interval is not strictly wider than the n=250
    interval.
    """
    lo_small, hi_small = confidence_interval(0.6, 20)
    lo_large, hi_large = confidence_interval(0.6, 250)

    assert (hi_small - lo_small) > (hi_large - lo_large)


def test_confidence_interval_handles_perfect_correlation_without_raising():
    """D-95's own recorded artefact: NATL/LINE measured at exactly +1.000
    before its screen removed it. The raw Fisher z-transform is undefined at
    `|corr| == 1.0` (`atanh(1.0)` diverges), so a `duplicate_series`-flagged
    edge -- which this module deliberately still returns, not drops -- must
    not be able to crash `comovement_edges` by driving `corr` to exactly the
    boundary.

    Falsifies if: `confidence_interval(1.0, n)` raises, or returns a
    non-finite bound.
    """
    lo, hi = confidence_interval(1.0, 60)

    assert math.isfinite(lo)
    assert math.isfinite(hi)


def test_confidence_interval_brackets_the_point_estimate():
    """A confidence interval that does not contain its own point estimate is
    not a confidence interval.

    Falsifies if: `corr` falls outside `[ci_low, ci_high]`.
    """
    corr, n = 0.45, 100

    lo, hi = confidence_interval(corr, n)

    assert lo <= corr <= hi


def test_confidence_interval_matches_the_fisher_z_formula():
    """Pins the exact construction (95% Fisher z-transform interval, `z* = 1.96`,
    `se = 1/sqrt(n-3)`) at corr=0.6, n=250 -- computed here with
    `math.atanh`/`math.tanh`, not asserted against a copied-in constant.

    Falsifies if: either bound differs from the directly-computed Fisher
    value by more than floating-point tolerance -- i.e. a different formula,
    confidence level, or standard-error denominator was used.
    """
    corr, n = 0.6, 250
    z = math.atanh(corr)
    se = 1.0 / math.sqrt(n - 3)
    expected_lo = math.tanh(z - 1.96 * se)
    expected_hi = math.tanh(z + 1.96 * se)

    lo, hi = confidence_interval(corr, n)

    assert lo == pytest.approx(expected_lo, abs=1e-9)
    assert hi == pytest.approx(expected_hi, abs=1e-9)


# --- duplicate_flag -----------------------------------------------------------


def test_duplicate_flag_flags_identical_return_series():
    """Session-for-session identical returns (fraction of exactly-equal
    sessions = 100%, far above any reasonable threshold) is the NATL/LINE
    case: one price series stored twice.

    Falsifies if: this returns `None`.
    """
    rng = np.random.default_rng(0)
    ret = pd.Series(rng.normal(0, 0.01, 60))

    flag = duplicate_flag(ret, ret.copy())

    assert flag == "duplicate_series"


def test_duplicate_flag_does_not_flag_genuinely_correlated_but_distinct_series():
    """Share classes like GOOGL/GOOG move together but are not the same
    series -- continuous, independently-drawn returns essentially never tie
    exactly (verified: 0 of 60 sessions equal for this fixture), so this must
    never flag even though the pair is strongly correlated.

    Falsifies if: this returns anything other than `None`.
    """
    n = 60
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(0, 0.01, n))
    b = 0.9 * a + pd.Series(rng.normal(0, 0.003, n))
    assert (a.to_numpy() == b.to_numpy()).mean() == 0.0, "fixture must not accidentally tie"

    flag = duplicate_flag(a, b)

    assert flag is None


# --- return sanity guard (implausible single-session returns) ---------------
#
# Measured on `data/bars-10y.parquet`: 11 sessions carry `|pct_change| > 10`
# (moves over 1000%) -- bankruptcy emergences, reverse splits and ticker reuse
# overwriting a price series, not market moves. Re-running D-95's headline
# measurement with those 11 rows masked moves `corr(discovery, validation)`
# from 0.640 to 0.586: the conclusion survives, but the published figure is
# inflated by data errors. `comovement_edges` must exclude such sessions from
# its correlation, not feed them to Pearson `corr` as if they were real.


def _corrupted_return_closes(
    n: int = 60, corrupt_idx: int = 30, corrupt_return: float = 500.0
) -> tuple[pd.DataFrame, date, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Two independent 2-symbol blocs, same construction as `_two_bloc_closes`,
    except A's return at session `corrupt_idx` is overwritten with an
    implausible `corrupt_return` (a raw pct_change value, e.g. `500.0` for a
    500x single-session move -- the D-95 data-artefact shape: 45x-526x in the
    real file). B, C and D are left clean throughout, and C-D is left
    untouched by the corruption entirely, so a test can check the screen is
    scoped to the affected pair, not applied window-wide.

    Returns `(closes, as_of, a, b, c, d)` where `a` is A's *clean* return
    array (without the corruption), so a caller can compute the correlation
    the corrupt session is expected to be excluded from.
    """
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.01, n)
    b = 0.9 * a + rng.normal(0, 0.003, n)
    c = rng.normal(0, 0.01, n)
    d = 0.9 * c + rng.normal(0, 0.003, n)
    a_corrupt = a.copy()
    a_corrupt[corrupt_idx] = corrupt_return

    idx = pd.bdate_range("2026-01-01", periods=n + 2, tz="UTC")
    as_of = idx[n + 1].date()
    r = {
        "A": np.concatenate([[0.0], a_corrupt, [0.0]]), "B": np.concatenate([[0.0], b, [0.0]]),
        "C": np.concatenate([[0.0], c, [0.0]]), "D": np.concatenate([[0.0], d, [0.0]]),
    }
    closes = pd.DataFrame({k: 100 * np.cumprod(1 + v) for k, v in r.items()}, index=idx)
    return closes, as_of, a, b, c, d


def test_comovement_edges_masks_implausible_single_session_return_before_correlating():
    """A single 500x session (raw pct_change = 500.0) must be excluded from
    the correlation, not fed into Pearson `corr` as a real return. Verified
    directly with numpy on this exact fixture: leaving the corrupt session in
    collapses corr(A, B) from +0.930 to -0.171 -- the pair would not even
    clear `min_abs_corr=0.3` -- while excluding just that one session
    recovers +0.930, matching the relationship the fixture actually
    engineered.

    Masking rather than clipping also means the affected pair is measured
    over one fewer session than an unaffected pair from the same window and
    call: A-B's `n_sessions` must be exactly one less than C-D's (C-D is
    never touched by the corruption), pinning "masked, not clipped" as an
    observable, per-pair session count rather than a window-wide one.

    Falsifies if: the A-B edge is missing entirely (today's behaviour -- the
    unmasked correlation collapses below threshold and the edge vanishes),
    its `corr` differs from the clean numpy computation, or its `n_sessions`
    is not exactly `trail - 1` while C-D's stays at `trail`.
    """
    n = 60
    corrupt_idx = 30
    closes, as_of, a, b, c, d = _corrupted_return_closes(n=n, corrupt_idx=corrupt_idx)
    expected_ab = float(np.corrcoef(np.delete(a, corrupt_idx), np.delete(b, corrupt_idx))[0, 1])
    assert expected_ab > 0.5, "fixture must engineer a real correlation to test against"

    edges = comovement_edges(closes, as_of, trail=n, min_abs_corr=0.3)

    pairs = {frozenset((e.a, e.b)): e for e in edges}
    assert frozenset(("A", "B")) in pairs, (
        "a single corrupt 500x session must not be allowed to erase a real A-B relationship")
    ab = pairs[frozenset(("A", "B"))]
    assert ab.corr == pytest.approx(expected_ab, abs=1e-9)
    assert frozenset(("C", "D")) in pairs, (
        "C-D is untouched by the corruption and must still qualify")
    cd = pairs[frozenset(("C", "D"))]
    assert cd.n_sessions == n
    assert ab.n_sessions == n - 1


def test_comovement_edges_does_not_screen_out_a_genuine_large_move():
    """The over-screening guard: a real single-session -30% drop (raw
    pct_change = -0.30) is orders of magnitude below any defensible
    "implausible return" cut -- the artefacts this screen targets sit at
    45x-526x in the real data, a very wide gap from a legitimate 30% move --
    and must be measured exactly as if no screen existed at all: included in
    both the correlation and the session count.

    Falsifies if: `corr` or `n_sessions` differs from the values computed
    with the -30% session included, which would mean a threshold set too
    tight is masking real, tradeable volatility along with the data
    artefacts it is meant to catch.
    """
    n = 60
    shock_idx = 30
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.01, n)
    y = 0.9 * x + rng.normal(0, 0.003, n)
    x_shocked = x.copy()
    x_shocked[shock_idx] = -0.30
    expected_xy = float(np.corrcoef(x_shocked, y)[0, 1])
    assert abs(expected_xy) > 0.3, "fixture must still clear the threshold to test against"

    idx = pd.bdate_range("2026-01-01", periods=n + 2, tz="UTC")
    as_of = idx[n + 1].date()
    r = {"X": np.concatenate([[0.0], x_shocked, [0.0]]), "Y": np.concatenate([[0.0], y, [0.0]])}
    closes = pd.DataFrame({k: 100 * np.cumprod(1 + v) for k, v in r.items()}, index=idx)

    edges = comovement_edges(closes, as_of, trail=n, min_abs_corr=0.3)

    assert edges, "fixture must produce the X-Y edge for this test to mean anything"
    xy = {frozenset((e.a, e.b)): e for e in edges}[frozenset(("X", "Y"))]
    assert xy.corr == pytest.approx(expected_xy, abs=1e-9)
    assert xy.n_sessions == n


# --- min_abs_corr resource-exhaustion guard ----------------------------------
#
# `min_abs_corr=0` (or lower, or non-finite) makes `comovement_edges` compute
# and return the full pairwise matrix: measured on the real 2,183-symbol data
# this is ~2.38 million edges from ~100s of CPU and ~3.5GB RSS, versus 23,855
# edges in 3.9s at the live UI's own default of 0.5. `abs(c) < nan` is always
# `False` in Python, so a NaN threshold is not merely "a bad request" -- it
# silently behaves exactly like 0, with no exception anywhere to catch. This
# runs on a single-node cluster that also carries the owner's real-money
# trading system, so driving it to OOM is not a local slowdown, it degrades
# a neighbour. A floor of 0.1 is pinned below -- matching the live UI
# slider's own minimum (`scripts/serve_index.html:268`, `min="0.1"`), so
# every value the UI can ever send survives unclamped.


def test_comovement_edges_clamps_non_positive_min_abs_corr_to_the_floor():
    """`min_abs_corr=0.0` must be clamped up to the 0.1 floor, not left to
    match every pair whose |corr| is merely non-negative. This fixture's
    cross-bloc pairs all correlate below 0.1 in magnitude (verified above
    with numpy: |corr(A,C)|=0.040, |corr(A,D)|=0.025, |corr(B,C)|=0.043,
    |corr(B,D)|=0.009), so an unclamped call would return all 6 pairs while
    a floor-respecting call returns only the 2 intra-bloc pairs -- exactly
    matching an explicit `min_abs_corr=0.1` call.

    Falsifies if: `min_abs_corr=0.0` returns more than 2 edges, or an edge
    set/values that differ from calling with `min_abs_corr=0.1` explicitly --
    either would mean 0.0 reached the correlation loop unclamped.
    """
    closes, as_of, expected = _two_bloc_closes()
    for cols in [("A", "C"), ("A", "D"), ("B", "C"), ("B", "D")]:
        c = float(np.corrcoef(expected[cols[0]], expected[cols[1]])[0, 1])
        assert abs(c) < 0.1, f"fixture's {cols} pair must sit below the floor to test clamping"

    unclamped = comovement_edges(closes, as_of, trail=60, min_abs_corr=0.0)
    floored = comovement_edges(closes, as_of, trail=60, min_abs_corr=0.1)

    assert len(unclamped) == 2, "min_abs_corr=0.0 must be clamped, not left to match every pair"
    pairs_unclamped = {frozenset((e.a, e.b)): (e.corr, e.n_sessions) for e in unclamped}
    pairs_floored = {frozenset((e.a, e.b)): (e.corr, e.n_sessions) for e in floored}
    assert pairs_unclamped == pairs_floored


def test_comovement_edges_clamps_negative_min_abs_corr_to_the_floor():
    """A negative `min_abs_corr` is nonsensical -- it is a threshold on
    `|corr|`, which is never negative -- and must be clamped exactly like
    0.0, not treated as an even-looser-than-zero threshold that still lets
    every pair through.

    Falsifies if: `min_abs_corr=-1.0` returns a different edge count or edge
    set than `min_abs_corr=0.1` (proving the floor only holds at exactly
    0.0, not below it).
    """
    closes, as_of, _ = _two_bloc_closes()

    negative = comovement_edges(closes, as_of, trail=60, min_abs_corr=-1.0)
    floored = comovement_edges(closes, as_of, trail=60, min_abs_corr=0.1)

    assert len(negative) == 2
    pairs_negative = {frozenset((e.a, e.b)): (e.corr, e.n_sessions) for e in negative}
    pairs_floored = {frozenset((e.a, e.b)): (e.corr, e.n_sessions) for e in floored}
    assert pairs_negative == pairs_floored


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_comovement_edges_rejects_non_finite_min_abs_corr(bad):
    """`abs(c) < nan` is always `False`, so a naive comparison lets a NaN
    threshold silently compute the entire pairwise matrix -- worse than 0.0,
    since there is no numeric ordering to clamp against. `+-inf` is equally
    never a legitimate request (the UI's slider is bounded 0.1-0.95). Both
    must be rejected outright, not silently reinterpreted as some other
    number.

    Falsifies if: this does not raise `ValueError`.
    """
    closes, as_of, _ = _two_bloc_closes()

    with pytest.raises(ValueError):
        comovement_edges(closes, as_of, trail=60, min_abs_corr=bad)


# --- session_available (PHASE-1, PLAN-2026-09-09-ingest-coherence) -----------
#
# `session_available(closes, as_of, trail)` reuses `comovement_edges`'s own
# two silent-`[]` conditions -- `as_of` absent from the index, or fewer than
# `trail` sessions precede it -- and turns them into an explicit `(bool, str)`
# result a caller can act on, without changing `comovement_edges`'s existing
# return-`[]` contract. Does not exist yet in `lagmatrix.comovement`; imported
# locally inside each test below (not at module scope) so this file's other,
# already-passing tests do not collect-error alongside it.


def test_session_available_true_when_trail_sessions_precede_as_of():
    """300-row synthetic frame, `as_of` at row 280: exactly 280 sessions
    precede it, comfortably above `trail=250` -- the fully-available case.

    Falsifies if: this returns `False`, or the message is non-empty for a
    window that is, in fact, fully available.
    """
    from lagmatrix.comovement import session_available

    idx = pd.bdate_range("2026-01-01", periods=300, tz="UTC")
    closes = pd.DataFrame({"A": np.arange(300, dtype=float)}, index=idx)
    as_of = idx[280].date()

    result = session_available(closes, as_of, trail=250)

    assert result == (True, "")


def test_session_available_false_when_as_of_is_absent():
    """`as_of` one calendar day past the frame's last index entry -- absent
    from `closes.index` entirely. This is the exact shape of the real
    incident: `bars-10y.parquet` frozen at 2026-09-04 while `as_of` resolves
    to 2026-09-09.

    Falsifies if: this returns `True`, or the message names neither the
    requested date nor the frame's actual last session (a caller needs both
    to diagnose the failure, not just a bare `False`).
    """
    from lagmatrix.comovement import session_available

    idx = pd.bdate_range("2026-01-01", periods=300, tz="UTC")
    closes = pd.DataFrame({"A": np.arange(300, dtype=float)}, index=idx)
    last_session = idx[-1].date()
    as_of = last_session + timedelta(days=1)

    ok, msg = session_available(closes, as_of, trail=250)

    assert ok is False
    assert str(as_of) in msg
    assert str(last_session) in msg


def test_session_available_false_when_trail_sessions_do_not_precede_as_of():
    """`as_of` at row 10 of a 300-row frame: present in the index, but only
    10 sessions precede it, not the requested `trail=250`.

    Falsifies if: this returns `True`, or the message does not name the
    number of sessions that actually precede `as_of` (10) -- a message that
    only ever repeated the requested `trail` back would be useless for
    telling "10 available" apart from "0 available".
    """
    from lagmatrix.comovement import session_available

    idx = pd.bdate_range("2026-01-01", periods=300, tz="UTC")
    closes = pd.DataFrame({"A": np.arange(300, dtype=float)}, index=idx)
    as_of = idx[10].date()

    ok, msg = session_available(closes, as_of, trail=250)

    assert ok is False
    assert "10" in msg
