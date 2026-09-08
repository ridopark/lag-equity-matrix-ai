"""PHASE-2 regression: the `news` state channel was retyped to
`Annotated[dict[str, list[NewsChunk]], _merge]` (keyed like `lag_edges_by_key`
/ `leader_shocks` / `evidence_by_key`) but `vector_retriever.retrieve_news`
still returns a flat `{"news": [...]}` list. `_merge` does `{**a, **b}`, which
raises `TypeError: 'list' object is not a mapping` the moment two branches
(or even one) write into the `news` channel — so the news branch cannot run
at all today.

PHASE-7 retargets this file's fake from the old direct Alpaca `NewsClient` to
the real semantic index's `NewsIndex.search(query, symbols, limit)`
(`src/lagmatrix/adapters/vector.py`), injected via
`LagMatrixContext.vector_index` — the reserved injection point that lets a
fake stand in for the real index, matching `retrieve_news`'s actual
collaborator now that PHASE-7 deleted the module's `NewsClient` import.
Injection replaces the old monkeypatch of that import, which no longer has
anything to patch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from lagmatrix.domain.models import Candidate, NewsChunk
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import candidate_key

# topk=3 so CAND (LEAD1/LEAD2 bloc) and CAND2 (LEAD4/LEAD5 bloc) have disjoint
# neighbourhoods and both reach context_fusion/assessor — see test_fanout.py.
TOPK = 3


def _candidate(sym, d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def _chunk(doc_id: str, symbol: str) -> NewsChunk:
    """Stands in for one row `NewsIndex.search` maps into a `NewsChunk`."""
    return NewsChunk(
        doc_id=doc_id,
        symbol=symbol,
        text=f"{symbol} headline {doc_id}",
        published_at=datetime(2026, 5, 30, tzinfo=UTC),
        score=1.0,
    )


class FakeNewsIndex:
    """Stands in for `lagmatrix.adapters.vector.NewsIndex`. Filters by
    `symbols[0]` the same way the real index's AQL `INTERSECTION` filter
    would scope results to the requested symbol, so each candidate's fetch
    only ever returns its own articles."""

    def __init__(self, chunks_by_symbol: dict[str, list[NewsChunk]]):
        self._chunks_by_symbol = chunks_by_symbol

    def search(self, query, symbols, limit, as_of=None):
        return self._chunks_by_symbol.get(symbols[0], [])


@pytest.fixture
def fake_vector_index():
    """One article per symbol, injected via `LagMatrixContext.vector_index`
    (the reserved injection point) so this test never dials out to a real
    ArangoDB/embedding model."""
    return FakeNewsIndex({"CAND": [_chunk("n1", "CAND")], "CAND2": [_chunk("n2", "CAND2")]})


async def _invoke(closes, candidates, vector_index):
    g = build_graph(with_news=True)
    ctx = LagMatrixContext(
        closes=closes, signal_universe=set(), topk=TOPK, vector_index=vector_index
    )
    return await g.ainvoke({"candidates": candidates}, context=ctx)


async def test_news_branch_runs_with_injected_client(closes, fake_vector_index):
    """The graph must complete `with_news=True` using the injected fake and
    produce an assessment.

    Falsifies if: `g.invoke` raises (today: `TypeError: 'list' object is not
    a mapping` from `_merge` in `graph/state.py`, because `retrieve_news`
    returns a flat list for the dict-typed `news` channel), or the run
    completes but `out["assessments"]` is empty.
    """
    out = await _invoke(closes, [_candidate("CAND")], fake_vector_index)
    assert out["assessments"]


async def test_news_is_keyed_per_candidate_not_cross_attributed(closes, fake_vector_index):
    """`news` must be keyed by `candidate_key`, like `lag_edges_by_key` /
    `leader_shocks` / `evidence_by_key` -- CAND's entry must hold only CAND's
    article and CAND2's entry only CAND2's, even though both share one batch
    and one `_merge`d channel.

    Falsifies if: `out["news"]` is missing either candidate's key, either
    key's articles include the other candidate's symbol, or a key's article
    count is anything other than exactly the one article that candidate's
    fake fetch returned (e.g. both candidates colliding on one key, or one
    candidate's branch overwriting the other's entry).
    """
    cand, cand2 = _candidate("CAND"), _candidate("CAND2")
    out = await _invoke(closes, [cand, cand2], fake_vector_index)

    news = out["news"]
    cand_chunks = news[candidate_key(cand)]
    cand2_chunks = news[candidate_key(cand2)]

    assert {n.symbol for n in cand_chunks} == {"CAND"}
    assert {n.symbol for n in cand2_chunks} == {"CAND2"}
    assert len(cand_chunks) == 1
    assert len(cand2_chunks) == 1
