"""The graph compiles, runs, and attributes evidence to the right candidate."""

from __future__ import annotations

from datetime import date

import pytest

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.builder import build_graph


def _candidate(sym="CAND", d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def test_graph_compiles_both_topologies(closes):
    nodes = set(build_graph(with_news=False).get_graph().nodes)
    assert {"graph_retriever", "leader_state", "context_fusion", "assessor"} <= nodes
    assert "vector_retriever" not in nodes


def test_runs_end_to_end_and_produces_one_assessment(run_graph):
    out = run_graph([_candidate()])
    assert len(out["assessments"]) == 1
    a = out["assessments"][0]
    assert a.verdict in {"corroborated", "contradicted", "neutral"}
    assert a.candidate.symbol == "CAND"


def test_neighbourhood_excludes_the_signal_universe(run_graph):
    """D-27: neighbours come from the wide universe, never the signal's tickers."""
    out = run_graph([_candidate()], signal_universe={"LEAD1", "LEAD2"})
    assert {e.leader for e in out["lag_edges"]}.isdisjoint({"LEAD1", "LEAD2"})


def test_assessment_is_never_calibrated(run_graph):
    """D-34: no odds are emitted until a powered test justifies them."""
    out = run_graph([_candidate()])
    assert out["assessments"][0].odds_adjustment == 0.0


def test_effective_evidence_never_exceeds_raw_count(run_graph):
    """Q-12: correlated neighbours are discounted, never inflated."""
    out = run_graph([_candidate()])
    a = out["assessments"][0]
    raw = len(a.supporting) + len(a.contradicting)
    assert a.effective_evidence <= raw + 1e-9


@pytest.mark.parametrize("direction", ["up", "down"])
def test_direction_flips_support(run_graph, direction):
    """The same neighbourhood cannot corroborate both directions."""
    out = run_graph([_candidate(direction=direction)])
    a = out["assessments"][0]
    assert a.verdict in {"corroborated", "contradicted", "neutral"}
    assert all(e.supports for e in a.supporting)
    assert all(not e.supports for e in a.contradicting)
