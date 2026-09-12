"""PHASE-2: `Send` fan-out keeps candidates' evidence from cross-attributing.

Today a single batched `invoke` shares `effective_evidence` and the flat
`evidence` list across every candidate in state — `assess()` reads the whole
list for each candidate, not just its own. These tests pin the per-candidate
isolation that PHASE-2's `Send`-per-branch topology must provide.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from conftest import invoke_graph
from lagmatrix.domain.models import Candidate
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext

# topk=3 so each candidate's neighbourhood is its own bloc, not every column in
# the tiny synthetic universe: at topk=20 (the default) every candidate's
# "neighbourhood" would be all the columns regardless of correlation, which
# would make CAND and CAND2's neighbourhoods overlap even under a correct
# implementation and the disjointness assertions meaningless.
#
# This comment used to read "self + its two leaders". That was the Q-26 bug
# talking: the candidate correlated with itself at rho=1.0 and took a slot in
# its own top-k. Since D-54 it is excluded, so a bloc here is the two real
# leaders.
TOPK = 3


def _candidate(sym, d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def _invoke(closes, candidates):
    g = build_graph(with_news=False)
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK)
    return invoke_graph(g, {"candidates": candidates}, context=ctx)


def _two_bloc_closes() -> pd.DataFrame:
    """Two independent blocs, each with a genuine shock, temporally separated
    so neither candidate's own correlation/baseline window ever sees the
    other bloc's data.

    CAND's story lives in sessions 1-60 (LEAD1/LEAD2 shock upward in the last
    `move_win=3` of that `trail=60` window; CAND itself stays quiet); LEAD4,
    LEAD5 and CAND2 are held flat (return 0) throughout, so their correlation
    with CAND is undefined (zero variance) and they are dropped from CAND's
    pool -- not merely outranked. CAND2's story is the mirror image in
    sessions 61-120, with LEAD1/LEAD2/CAND flat there instead. Session 0 is a
    throwaway base row and session 121 exists only so a session "after"
    CAND2's `as_of` is available (D-16 point-in-time check).

    Without this separation, a real shock big enough to clear sigma=2.0 in
    both blocs at once dominates the correlation computed over their shared
    trailing window enough to pull a leader from one candidate's bloc into
    the other's neighbourhood -- an artefact of Pearson correlation being
    swamped by a few outlier sessions, not real cross-attribution, but
    indistinguishable from it if left in.
    """
    rng = np.random.default_rng(0)
    n_quiet = 57

    common_a = rng.normal(0, 0.01, n_quiet)
    lead1_q = common_a + rng.normal(0, 0.001, n_quiet)
    lead2_q = common_a + rng.normal(0, 0.001, n_quiet)
    cand_q = common_a * 0.8 + rng.normal(0, 0.004, n_quiet)
    shock_a = [0.05, 0.06, 0.04]
    lead1_seg1 = list(lead1_q) + shock_a
    lead2_seg1 = list(lead2_q) + [x * 0.95 for x in shock_a]
    cand_seg1 = list(cand_q) + [0.0005, 0.0004, 0.0003]

    common_b = rng.normal(0, 0.01, n_quiet)
    lead4_q = common_b + rng.normal(0, 0.001, n_quiet)
    lead5_q = common_b + rng.normal(0, 0.001, n_quiet)
    cand2_q = common_b * 0.8 + rng.normal(0, 0.004, n_quiet)
    shock_b = [0.045, 0.055, 0.035]
    lead4_seg2 = list(lead4_q) + shock_b
    lead5_seg2 = list(lead5_q) + [x * 0.95 for x in shock_b]
    cand2_seg2 = list(cand2_q) + [0.0004, 0.0003, 0.0002]

    zeros60 = [0.0] * 60
    r = {
        "LEAD1": [0.0, *lead1_seg1, *zeros60, 0.0],
        "LEAD2": [0.0, *lead2_seg1, *zeros60, 0.0],
        "CAND": [0.0, *cand_seg1, *zeros60, 0.0],
        "LEAD4": [0.0, *zeros60, *lead4_seg2, 0.0],
        "LEAD5": [0.0, *zeros60, *lead5_seg2, 0.0],
        "CAND2": [0.0, *zeros60, *cand2_seg2, 0.0],
    }
    idx = pd.bdate_range("2026-01-01", periods=122, tz="UTC")
    return pd.DataFrame({k: 100 * np.cumprod([1 + x for x in v]) for k, v in r.items()}, index=idx)


# The inactive bloc is held flat in each half, so its variance is exactly zero and
# its cross-bloc correlation is NaN by construction rather than merely small. numpy
# reports the 0/0 while computing that; the NaN is the point, so the warning is
# expected rather than a symptom.
@pytest.mark.filterwarnings("ignore:invalid value encountered in divide:RuntimeWarning")
def test_two_candidates_do_not_cross_attribute():
    """CAND (LEAD1/LEAD2 bloc) and CAND2 (LEAD4/LEAD5 bloc) are uncorrelated;
    each assessment's evidence must come only from its own bloc.

    Falsifies if: an assessment's supporting/contradicting evidence includes a
    symbol drawn from the *other* candidate's bloc — i.e. the two sets stop
    being disjoint. Also falsifies (Q-26) if either candidate's own symbol
    shows up in its own evidence: neither candidate is excluded via
    `signal_universe` here, so a regressed self-edge would surface as a
    guaranteed contradicting unit without crossing into the other's bloc,
    which the disjointness check alone would miss.
    """
    closes = _two_bloc_closes()
    candidates = [
        _candidate("CAND", d=date(2026, 3, 26)),
        _candidate("CAND2", d=date(2026, 6, 18)),
    ]
    out = _invoke(closes, candidates)
    by_symbol = {a.candidate.symbol: a for a in out["assessments"]}
    a1, a2 = by_symbol["CAND"], by_symbol["CAND2"]

    syms1 = {e.symbol for e in a1.supporting + a1.contradicting}
    syms2 = {e.symbol for e in a2.supporting + a2.contradicting}

    assert syms1, "expected CAND to have evidence from its own bloc"
    assert syms2, "expected CAND2 to have evidence from its own bloc"
    assert syms1.isdisjoint(syms2)
    assert "CAND" not in syms1
    assert "CAND2" not in syms2


def test_duplicate_candidates_each_get_an_assessment(closes):
    """The same `Candidate` submitted twice still yields two assessments, both
    equal to a single-candidate run — the key dedupes work, not output.

    Falsifies if: `len(assessments) != 2`, or either duplicate's verdict /
    effective_evidence differs from what a lone invocation of that candidate
    produces (today the un-deduped work is done twice, doubling
    `effective_evidence` relative to the single-candidate baseline).
    """
    cand = _candidate("CAND")
    single = _invoke(closes, [cand])
    duped = _invoke(closes, [cand, cand])

    assert len(duped["assessments"]) == 2
    baseline = single["assessments"][0]
    for a in duped["assessments"]:
        assert a.verdict == baseline.verdict
        assert a.effective_evidence == baseline.effective_evidence


def test_batched_matches_per_candidate_loop(closes):
    """One batched invoke of three candidates must match three separate
    single-candidate invokes on (symbol, verdict, effective_evidence).

    Falsifies if: the batched set differs from the looped set — e.g. a shared
    `effective_evidence` scalar or cross-attributed evidence changes a
    candidate's verdict or evidence weight depending on who else was batched
    with it.
    """
    candidates = [_candidate("CAND"), _candidate("CAND2"), _candidate("INDEP")]

    batched = _invoke(closes, candidates)
    batched_set = {
        (a.candidate.symbol, a.verdict, a.effective_evidence) for a in batched["assessments"]
    }

    looped_set = set()
    for c in candidates:
        single = _invoke(closes, [c])
        looped_set.update(
            (a.candidate.symbol, a.verdict, a.effective_evidence)
            for a in single["assessments"]
        )

    assert batched_set == looped_set


def test_candidate_without_neighbourhood_is_skipped(closes):
    """A candidate whose symbol is absent from `closes` gets an `errors` entry
    and no assessment; a valid candidate batched alongside it still gets one.

    Falsifies if: the ghost candidate receives an assessment anyway (today
    `assess()` emits one for every entry in `state["candidates"]`
    unconditionally, regardless of whether that candidate's own branch found a
    neighbourhood), or the valid candidate's assessment goes missing.
    """
    out = _invoke(closes, [_candidate("GHOST"), _candidate("CAND")])

    assert any("GHOST" in e for e in out["errors"])
    symbols = {a.candidate.symbol for a in out["assessments"]}
    assert "GHOST" not in symbols
    assert "CAND" in symbols
