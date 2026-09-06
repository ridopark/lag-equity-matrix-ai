"""PHASE-2: `Send` fan-out keeps candidates' evidence from cross-attributing.

Today a single batched `invoke` shares `effective_evidence` and the flat
`evidence` list across every candidate in state — `assess()` reads the whole
list for each candidate, not just its own. These tests pin the per-candidate
isolation that PHASE-2's `Send`-per-branch topology must provide.
"""

from __future__ import annotations

from datetime import date

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext

# topk=3 so each candidate's neighbourhood is its own bloc (self + its two
# leaders), not every column in the tiny synthetic universe — see the
# correlation probe in the session notes: at topk=20 (the default) every
# candidate's "neighbourhood" is all 8 columns regardless of correlation,
# which would make CAND and CAND2's neighbourhoods overlap even under a
# correct implementation and the disjointness assertions meaningless.
TOPK = 3


def _candidate(sym, d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def _invoke(closes, candidates):
    g = build_graph(with_news=False)
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK)
    return g.invoke({"candidates": candidates}, context=ctx)


def test_two_candidates_do_not_cross_attribute(closes):
    """CAND (LEAD1/LEAD2 bloc) and CAND2 (LEAD4/LEAD5 bloc) are uncorrelated;
    each assessment's evidence must come only from its own bloc.

    Falsifies if: an assessment's supporting/contradicting evidence includes a
    symbol drawn from the *other* candidate's bloc — i.e. the two sets stop
    being disjoint.
    """
    out = _invoke(closes, [_candidate("CAND"), _candidate("CAND2")])
    by_symbol = {a.candidate.symbol: a for a in out["assessments"]}
    a1, a2 = by_symbol["CAND"], by_symbol["CAND2"]

    syms1 = {e.symbol for e in a1.supporting + a1.contradicting}
    syms2 = {e.symbol for e in a2.supporting + a2.contradicting}

    assert syms1, "expected CAND to have evidence from its own bloc"
    assert syms2, "expected CAND2 to have evidence from its own bloc"
    assert syms1.isdisjoint(syms2)


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
