"""RED for PHASE-1 (PLAN-2026-09-11-quant-daytrade-perspectives):
`compute_quant_perspective`, the deterministic quant node.

`QuantPerspective` and `compute_quant_perspective` do not exist yet -- TASK-1.4
and TASK-1.5 belong to the green phase. These tests pin three things the
green implementation must satisfy:

* it must call `lagmatrix.comovement.confidence_interval` /
  `.duplicate_flag` rather than re-deriving the Fisher interval or the
  duplicate-series check itself (DRY, TASK-1.1), and must do so only for
  `relation == "correlation"` edges;
* it must expose a within-window split-half sign-stability check
  (TASK-1.2) that is *not* D-93's discovery/validation split -- that split
  spans years and lives in `capture_baseline.py`/D-93's own measurement
  scripts. This one runs inside a single daily invocation, splitting one
  candidate's own trailing correlation window in half;
* it must flag a candidate symbol found in the excluded-ETF set
  (TASK-1.3), independent of `data/excluded-etfs.csv` -- the set is always
  injected as `excluded_etfs`, never read from disk here.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from lagmatrix.domain.models import Candidate, LagEdge, QuantPerspective
from lagmatrix.graph.nodes import quant_perspective as qp_module
from lagmatrix.graph.nodes.quant_perspective import PMI_STRONG_THRESHOLD, compute_quant_perspective


def _candidate(symbol: str = "CAND", as_of: date = date(2026, 6, 1)) -> Candidate:
    return Candidate(symbol=symbol, direction="up", as_of=as_of, origin="external")


def _corr_edge(leader: str, lagger: str, correlation: float) -> LagEdge:
    return LagEdge(
        leader=leader, lagger=lagger, correlation=correlation, lag_days=0,
        beta=0.3, relation="correlation",
    )


def test_confidence_interval_and_duplicate_flag_come_from_comovement_not_reimplemented(
    closes, monkeypatch
):
    """DRY (TASK-1.1): every correlation edge must go through
    `lagmatrix.comovement.confidence_interval` / `.duplicate_flag` -- not a
    second, independent computation of the same Fisher interval or
    duplicate-series test inside `quant_perspective.py`.

    Patches both names where `quant_perspective` looks them up (not where
    they are defined) and counts calls -- an import-only check would still
    pass even if the function imported them and then ignored them in favour
    of its own math, so this checks the call actually happens, once per
    correlation edge.

    Falsifiable by: reimplementing either calculation inline in
    `compute_quant_perspective` instead of calling these two names -- the
    call counts below would drop to 0 and the test fails even though
    `n_edges`/`median_ci_width` might still look plausible.
    """
    calls = {"ci": 0, "dup": 0}

    def fake_ci(*args, **kwargs):
        calls["ci"] += 1
        return (0.1, 0.2)

    def fake_dup(*args, **kwargs):
        calls["dup"] += 1
        return None

    monkeypatch.setattr(qp_module, "confidence_interval", fake_ci)
    monkeypatch.setattr(qp_module, "duplicate_flag", fake_dup)

    candidate = _candidate()
    edges = [
        _corr_edge("LEAD1", "CAND", 0.5),
        _corr_edge("LEAD2", "CAND", 0.4),
        # not a correlation edge -- must be ignored entirely, including by
        # the two comovement calls above (relation filter, TASK-1.5).
        LagEdge(
            leader="CAND", lagger="SUPPLIER1", correlation=0.0, lag_days=1,
            beta=0.42, relation="supplier",
        ),
    ]

    result = compute_quant_perspective(
        candidate, edges, closes, trail=60, excluded_etfs=frozenset()
    )

    assert calls["ci"] == 2
    assert calls["dup"] == 2
    assert result.n_edges == 2


def test_split_half_sign_agreement_distinguishes_a_stable_pair_from_a_flipping_one():
    """TASK-1.2: within one candidate's own trailing window, split it in half
    and compare each correlation edge's sign across the two halves.

    This is NOT D-93's discovery/validation split -- that split holds out
    years of future data to measure replication, and runs offline in
    `capture_baseline.py`, never inside a single graph invocation. This
    check is much cheaper and much weaker: it only asks whether a pair's
    sign held between the first and second halves of the *same* trailing
    window that already fed `lag_edges`, entirely within one daily
    `compute_quant_perspective` call.

    STABLE tracks CAND's underlying return series (plus small independent
    noise) across the whole window, so its sign should hold in both halves.
    FLIP tracks CAND's return series in the first half and its exact
    negation in the second half, engineered as a deterministic transform
    (not a random draw) so the sign flip is exact rather than probable --
    verified directly with pandas below rather than assumed, matching this
    repo's own house rule (see `test_comovement_source.py`).

    Falsifiable by: computing `split_half_sign_agree_pct` from the edge's
    single full-window correlation sign (or from any measure that ignores
    the within-window split) -- STABLE and FLIP would then report the same
    value instead of the two below.
    """
    rng = np.random.default_rng(7)
    n = 61  # 60 trailing sessions + the as_of session itself, excluded (D-16)
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    base = rng.normal(0, 0.01, n)
    cand_ret = base.copy()
    stable_ret = base + rng.normal(0, 0.0005, n)
    flip_ret = base.copy()
    flip_ret[30:60] = -base[30:60]  # sign-flipped only within the trailing window
    closes = pd.DataFrame(
        {
            "CAND": 100 * np.exp(np.cumsum(cand_ret)),
            "STABLE": 100 * np.exp(np.cumsum(stable_ret)),
            "FLIP": 100 * np.exp(np.cumsum(flip_ret)),
        },
        index=idx,
    )
    as_of = idx[60].date()

    # Sanity-check the fixture actually engineers what this test needs,
    # computed directly rather than assumed.
    returns = closes.pct_change()
    window = returns.iloc[0:60]
    first_half, second_half = window.iloc[:30], window.iloc[30:]
    corr_first, corr_second = first_half.corr(), second_half.corr()
    assert np.sign(corr_first.loc["CAND", "STABLE"]) == np.sign(corr_second.loc["CAND", "STABLE"])
    assert np.sign(corr_first.loc["CAND", "FLIP"]) != np.sign(corr_second.loc["CAND", "FLIP"])

    full_corr = window.corr()
    candidate = _candidate(as_of=as_of)
    stable_edge = _corr_edge("STABLE", "CAND", float(full_corr.loc["CAND", "STABLE"]))
    flip_edge = _corr_edge("FLIP", "CAND", float(full_corr.loc["CAND", "FLIP"]))

    stable_result = compute_quant_perspective(
        candidate, [stable_edge], closes, trail=60, excluded_etfs=frozenset()
    )
    flip_result = compute_quant_perspective(
        candidate, [flip_edge], closes, trail=60, excluded_etfs=frozenset()
    )

    assert stable_result.split_half_sign_agree_pct == 100.0
    assert flip_result.split_half_sign_agree_pct == 0.0


def test_split_half_min_abs_reflects_the_weak_half_not_the_full_window_correlation():
    """Team lead's correction to TASK-1.2: `split_half_sign_agree_pct` is a
    sign bit and throws away the magnitude that carries most of the signal
    (measured: magnitude carries 2.5-3.5x the incremental AUC of the sign
    bit over the discovery correlation alone). `split_half_min_abs` must be
    `min(|corr_first_half|, |corr_second_half|)` -- the *weaker* of the two
    halves, not a re-statement of the full-window correlation the reader
    already sees on the edge.

    WEAK tracks CAND's return series tightly only in the second half of the
    trailing window; in the first half it is mostly independent noise, built
    as a deterministic mix (not a random draw of "maybe correlated") so the
    weak-half correlation is verified directly below rather than assumed.
    The full-window correlation is nonetheless substantial (~0.66) because
    the strong second half dominates it -- exactly the case where reporting
    the full-window number instead of the split-half minimum would hide
    that half the window carries almost no relationship at all.

    Falsifiable by: an implementation that sets `split_half_min_abs` to
    `abs(edge.correlation)` (the full-window value) or to the mean/max of
    the two halves instead of their min -- any of those would land near
    0.66, not near the ~0.30 this test asserts.
    """
    rng = np.random.default_rng(11)
    n = 61  # 60 trailing sessions + the as_of session itself, excluded (D-16)
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    base = rng.normal(0, 0.01, 60)
    indep = rng.normal(0, 0.01, 30)
    cand_ret = np.append(base, 0.0)  # as_of session's own return is unused
    weak_ret = np.append(
        np.concatenate([0.2 * base[:30] + 0.8 * indep, base[30:] + rng.normal(0, 0.0005, 30)]),
        0.0,
    )
    closes = pd.DataFrame(
        {
            "CAND": 100 * np.exp(np.cumsum(cand_ret)),
            "WEAK": 100 * np.exp(np.cumsum(weak_ret)),
        },
        index=idx,
    )
    as_of = idx[60].date()

    # Sanity-check the fixture actually engineers what this test needs,
    # computed directly rather than assumed.
    returns = closes.pct_change()
    window = returns.iloc[0:60]
    first_half, second_half = window.iloc[:30], window.iloc[30:]
    corr_first = first_half["CAND"].corr(first_half["WEAK"])
    corr_second = second_half["CAND"].corr(second_half["WEAK"])
    full_corr = window["CAND"].corr(window["WEAK"])
    assert abs(corr_first) < 0.35, "the first half must be the weak one"
    assert abs(corr_second) > 0.95, "the second half must be strong"
    assert full_corr > 0.6, "the full-window correlation must look strong"

    candidate = _candidate(as_of=as_of)
    edge = _corr_edge("WEAK", "CAND", float(full_corr))

    result = compute_quant_perspective(
        candidate, [edge], closes, trail=60, excluded_etfs=frozenset()
    )

    assert result.split_half_min_abs == pytest.approx(min(abs(corr_first), abs(corr_second)))
    assert result.split_half_min_abs < 0.4, (
        "must reflect the weak half, not the ~0.66 full-window correlation"
    )


def test_split_half_min_abs_distinguishes_consistently_strong_pair_from_one_half_only_pair():
    """A pair that correlates strongly in both halves and a pair that
    correlates strongly in only one half can carry identical
    `split_half_sign_agree_pct` (both signs agree, 100%) while their true
    support is very different -- `split_half_min_abs` must tell them apart,
    which is exactly the information the sign bit alone cannot express.

    STRONG tracks CAND tightly across the whole window (both halves
    strong). WEAK is the same fixture as the previous test: strong only in
    the second half, weak in the first -- but both pairs' correlations are
    positive in both halves, so sign agreement is 100% for both.

    Falsifiable by: computing `split_half_min_abs` from
    `split_half_sign_agree_pct` or any function of it alone -- STRONG and
    WEAK would then be indistinguishable despite the assertions below
    requiring they differ.
    """
    rng = np.random.default_rng(11)
    n = 61
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    base = rng.normal(0, 0.01, 60)
    indep = rng.normal(0, 0.01, 30)
    cand_ret = np.append(base, 0.0)
    weak_ret = np.append(
        np.concatenate([0.2 * base[:30] + 0.8 * indep, base[30:] + rng.normal(0, 0.0005, 30)]),
        0.0,
    )
    strong_ret = np.append(base + rng.normal(0, 0.0005, 60), 0.0)
    closes = pd.DataFrame(
        {
            "CAND": 100 * np.exp(np.cumsum(cand_ret)),
            "WEAK": 100 * np.exp(np.cumsum(weak_ret)),
            "STRONG": 100 * np.exp(np.cumsum(strong_ret)),
        },
        index=idx,
    )
    as_of = idx[60].date()

    returns = closes.pct_change()
    window = returns.iloc[0:60]
    first_half, second_half = window.iloc[:30], window.iloc[30:]

    def sign_agree(col: str) -> bool:
        a = first_half["CAND"].corr(first_half[col])
        b = second_half["CAND"].corr(second_half[col])
        return np.sign(a) == np.sign(b)

    assert sign_agree("WEAK") and sign_agree("STRONG"), (
        "both fixtures must agree in sign, so only the magnitude can distinguish them"
    )

    candidate = _candidate(as_of=as_of)
    full_corr = window.corr()
    weak_edge = _corr_edge("WEAK", "CAND", float(full_corr.loc["CAND", "WEAK"]))
    strong_edge = _corr_edge("STRONG", "CAND", float(full_corr.loc["CAND", "STRONG"]))

    weak_result = compute_quant_perspective(
        candidate, [weak_edge], closes, trail=60, excluded_etfs=frozenset()
    )
    strong_result = compute_quant_perspective(
        candidate, [strong_edge], closes, trail=60, excluded_etfs=frozenset()
    )

    assert weak_result.split_half_sign_agree_pct == strong_result.split_half_sign_agree_pct == 100.0
    assert weak_result.split_half_min_abs < 0.4
    assert strong_result.split_half_min_abs > 0.95
    assert weak_result.split_half_min_abs != strong_result.split_half_min_abs


def test_split_half_min_abs_is_none_with_no_correlation_edges(closes):
    """Consistent with `median_ci_width` and `split_half_sign_agree_pct`:
    with no correlation edges there are no halves to compare, so the field
    must be `None`, not `0.0` or some other sentinel that a downstream
    reader could mistake for "measured and found to be zero".

    Falsifiable by: defaulting the field to `0.0` (or any other non-`None`
    value) when `correlation_edges` is empty.
    """
    candidate = _candidate()

    result = compute_quant_perspective(
        candidate, [], closes, trail=60, excluded_etfs=frozenset()
    )

    assert result.split_half_min_abs is None


def test_candidate_in_excluded_etf_set_is_flagged_etf(closes):
    """TASK-1.3: `candidate_is_etf` reflects membership in the injected
    `excluded_etfs` set -- a stand-in for `data/excluded-etfs.csv`, passed
    as an argument rather than read from disk. Covers both the flagged and
    the unflagged candidate so the field cannot be a constant.

    Falsifiable by: hardcoding `candidate_is_etf=False` (or `True`), or by
    checking some other attribute (e.g. the candidate's `origin`) instead of
    membership in `excluded_etfs` -- one of the two assertions below would
    then fail.
    """
    excluded_etfs = frozenset({"AAPD", "AAPU"})

    etf_candidate = _candidate(symbol="AAPD")
    ordinary_candidate = _candidate(symbol="CAND")

    etf_result = compute_quant_perspective(
        etf_candidate, [], closes, trail=60, excluded_etfs=excluded_etfs
    )
    ordinary_result = compute_quant_perspective(
        ordinary_candidate, [], closes, trail=60, excluded_etfs=excluded_etfs
    )

    assert etf_result.candidate_is_etf is True
    assert ordinary_result.candidate_is_etf is False


class _FakeTopology:
    """RED for PHASE-5 (PLAN-2026-09-12-relatedness-and-sectors): stand-in
    for `ArangoTopology.sic_of`/`.comention_pmi` (PHASE-4) -- no I/O, returns
    whatever a test constructs, matching how `llm`/`bars` are faked elsewhere
    in this suite rather than reaching a live database.
    """

    def __init__(self, sic: dict[str, str] | None = None, pmi: dict[str, float] | None = None):
        self._sic = sic or {}
        self._pmi = pmi or {}

    def sic_of(self, symbols: list[str]) -> dict[str, str]:
        return {s: self._sic[s] for s in symbols if s in self._sic}

    def comention_pmi(self, symbol: str) -> dict[str, float]:
        return dict(self._pmi)


def test_arango_topology_none_disables_all_three_relatedness_fields(closes):
    """TASK-5.1: `arango_topology=None` must disable `sector_match_pct`,
    `comention_weak_count` and `comention_strong_count` together, the same
    "`None` disables the feature" contract every other optional
    context-derived value in this repo honours -- even with correlation
    edges present, so there is data the feature *could* have used.

    Falsifiable by: any of the three defaulting to `0`/`0.0` instead of
    `None` when the feature is off, which would be indistinguishable from
    "measured, found nothing."
    """
    candidate = _candidate()
    edges = [_corr_edge("LEAD1", "CAND", 0.5), _corr_edge("LEAD2", "CAND", 0.4)]

    result = compute_quant_perspective(
        candidate, edges, closes, trail=60, excluded_etfs=frozenset(), arango_topology=None
    )

    assert result.sector_match_pct is None
    assert result.comention_weak_count is None
    assert result.comention_strong_count is None


def test_sector_match_pct_counts_same_2digit_sic_prefix(closes):
    """TASK-5.2: `sector_match_pct` is the percentage of a candidate's
    correlation-edge leaders sharing the candidate's own 2-digit SIC major
    group -- CAND ("7372") matches LEAD1 ("7371", same "73" prefix) but not
    LEAD2 ("3674", different major group), so exactly 1 of 2 leaders match.

    Falsifiable by: comparing the full 4-digit SIC instead of the 2-digit
    major-group prefix -- `"7372" != "7371"` and `"7372" != "3674"` on the
    full code, which would give `0.0` instead of the `50.0` asserted here.
    """
    candidate = _candidate()
    edges = [_corr_edge("LEAD1", "CAND", 0.5), _corr_edge("LEAD2", "CAND", 0.4)]
    fake = _FakeTopology(sic={"CAND": "7372", "LEAD1": "7371", "LEAD2": "3674"})

    result = compute_quant_perspective(
        candidate, edges, closes, trail=60, excluded_etfs=frozenset(), arango_topology=fake
    )

    assert result.sector_match_pct == 50.0


def test_sector_match_pct_none_when_candidates_own_sic_unresolved(closes):
    """TASK-5.3: when the candidate's own SIC did not resolve (the ~0.1%
    unmatched case), `sector_match_pct` must be `None` -- there is no
    candidate-side sector to compare a leader against. Co-mention counts
    come from an independent data source and must still compute as
    ordinary ints, not be dragged down to `None` by the missing sector
    lookup.

    Falsifiable by: the whole relatedness read degrading to `None` together
    -- proves sector and co-mention are wrongly coupled through one shared
    "missing" branch instead of two independent ones.
    """
    candidate = _candidate()
    edges = [_corr_edge("LEAD1", "CAND", 0.5)]
    fake = _FakeTopology(
        sic={"LEAD1": "7371"},  # CAND itself is deliberately absent/unresolved
        pmi={"LEAD1": PMI_STRONG_THRESHOLD - 0.1},
    )

    result = compute_quant_perspective(
        candidate, edges, closes, trail=60, excluded_etfs=frozenset(), arango_topology=fake
    )

    assert result.sector_match_pct is None
    assert result.comention_weak_count == 1
    assert result.comention_strong_count == 0


def test_comention_three_state_encoding_never_collapses_to_one_number(closes):
    """TASK-5.4 -- the test this whole phase exists to make pass. D-136
    found co-mention PMI carries two OPPOSITE-signed effects (having an edge
    predicts failure; higher PMI among edged pairs predicts retention), and
    D-135 already made the mistake of averaging them into one column, which
    destroyed the signal (0.4803). "No co-mention edge at all" must be a
    DISTINCT THIRD STATE, not folded into "weak" as a PMI of zero would be.

    LEAD1's PMI is above `PMI_STRONG_THRESHOLD` (imported, never a
    hardcoded literal, so this test stays valid regardless of PHASE-2's
    exact measured number), LEAD2's is below it, and LEAD3 has a
    correlation edge but no entry at all in `comention_pmi`'s returned dict
    -- no co-mention edge exists for that pair.

    Falsifiable by: a future edit replacing the two counts with one
    signed/averaged number (the exact D-135 mistake this feature exists to
    not repeat), or treating a missing PMI entry as weak -- either would
    make `comention_weak_count + comention_strong_count == 3` (matching
    `n_edges`) instead of the `2` asserted below.
    """
    candidate = _candidate()
    edges = [
        _corr_edge("LEAD1", "CAND", 0.5),
        _corr_edge("LEAD2", "CAND", 0.4),
        _corr_edge("LEAD3", "CAND", 0.3),
    ]
    fake = _FakeTopology(
        pmi={
            "LEAD1": PMI_STRONG_THRESHOLD + 0.5,
            "LEAD2": PMI_STRONG_THRESHOLD - 0.5,
            # LEAD3 deliberately absent: no co-mention edge for that pair.
        }
    )

    result = compute_quant_perspective(
        candidate, edges, closes, trail=60, excluded_etfs=frozenset(), arango_topology=fake
    )

    assert result.n_edges == 3
    assert result.comention_strong_count == 1
    assert result.comention_weak_count == 1
    assert result.comention_weak_count + result.comention_strong_count == 2


def test_quant_perspective_model_round_trips_new_fields():
    """TASK-5.5 (Q-61 guard): construct `QuantPerspective` directly with the
    three new fields and assert the actual returned *values*, not
    `hasattr`/`is not None`. `extra='ignore'` (Q-61) would let a
    misspelled/missing field name construct successfully and silently drop
    the kwarg -- only reading the value back off the result catches that.

    Falsifiable by: any field name in the model not matching the
    constructor kwarg exactly -- construction would still succeed (Q-61),
    but the attribute access below would raise `AttributeError` instead of
    returning the value asserted.
    """
    result = QuantPerspective(
        n_edges=1,
        median_ci_width=0.1,
        duplicate_count=0,
        split_half_sign_agree_pct=100.0,
        split_half_min_abs=0.2,
        candidate_is_etf=False,
        note="x",
        sector_match_pct=50.0,
        comention_weak_count=1,
        comention_strong_count=2,
    )

    assert result.sector_match_pct == 50.0
    assert result.comention_weak_count == 1
    assert result.comention_strong_count == 2


def test_compute_quant_perspective_handles_a_non_normalized_session_index(closes):
    """Both production price files (`data/bars.parquet`,
    `data/bars-10y.parquet`) index sessions at 04:00 UTC, not midnight --
    `compute_quant_perspective` must locate the as-of session by date
    regardless of the intraday timestamp component, not require an exact
    midnight match.

    Falsifiable by: today's exact-timestamp lookup
    (`sessions.get_loc(pd.Timestamp(candidate.as_of, tz=sessions.tz))`),
    which raises `KeyError` against a 04:00-indexed frame -- observed below.
    """
    shifted = closes.copy()
    shifted.index = closes.index + pd.Timedelta(hours=4)

    trail = 60
    ti = 80
    as_of = shifted.index[ti].date()
    candidate = _candidate(as_of=as_of)
    edge = _corr_edge("LEAD1", "CAND", 0.5)

    result = compute_quant_perspective(
        candidate, [edge], shifted, trail=trail, excluded_etfs=frozenset()
    )

    assert result.n_edges == 1

    returns = shifted.pct_change()
    window = returns.iloc[ti - trail : ti]
    half = trail // 2
    first_half, second_half = window.iloc[:half], window.iloc[half:]
    corr_first = first_half["CAND"].corr(first_half["LEAD1"])
    corr_second = second_half["CAND"].corr(second_half["LEAD1"])
    expected_agree = 100.0 if np.sign(corr_first) == np.sign(corr_second) else 0.0

    assert result.split_half_sign_agree_pct == expected_agree
    assert result.split_half_min_abs == pytest.approx(min(abs(corr_first), abs(corr_second)))


def test_quant_perspective_window_is_identical_normalized_or_not(closes):
    """The session lookup must resolve to the *same* trailing window whether
    the price index is normalized to midnight or carries a 04:00 UTC
    intraday component (the shape of the real `data/bars*.parquet` files) --
    the fix must change only which timestamp is located, never which window
    is measured.

    Falsifiable by: a fix that resolves the as-of session to the row *after*
    `candidate.as_of` (e.g. copying `leader_state.py`'s
    `sessions[sessions > str(c.as_of)][0]` idiom) instead of an exact-date
    match -- that would shift the window by one session and pull the as-of
    day's own return into the correlation, a D-16 lookahead violation this
    test would catch as a mismatch between the midnight- and 04:00-indexed
    results.
    """
    trail = 60
    ti = 80
    as_of = closes.index[ti].date()
    candidate = _candidate(as_of=as_of)
    edge = _corr_edge("LEAD1", "CAND", 0.5)

    midnight_result = compute_quant_perspective(
        candidate, [edge], closes, trail=trail, excluded_etfs=frozenset()
    )

    shifted = closes.copy()
    shifted.index = closes.index + pd.Timedelta(hours=4)
    shifted_result = compute_quant_perspective(
        candidate, [edge], shifted, trail=trail, excluded_etfs=frozenset()
    )

    assert midnight_result.n_edges == shifted_result.n_edges
    assert midnight_result.median_ci_width == shifted_result.median_ci_width
    assert midnight_result.split_half_sign_agree_pct == shifted_result.split_half_sign_agree_pct
    assert midnight_result.split_half_min_abs == shifted_result.split_half_min_abs
