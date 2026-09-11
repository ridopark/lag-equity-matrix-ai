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

from lagmatrix.domain.models import Candidate, LagEdge
from lagmatrix.graph.nodes import quant_perspective as qp_module
from lagmatrix.graph.nodes.quant_perspective import compute_quant_perspective


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
