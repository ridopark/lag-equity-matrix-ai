"""PHASE-2 regression: the `news` state channel was retyped to
`Annotated[dict[str, list[NewsChunk]], _merge]` (keyed like `lag_edges_by_key`
/ `leader_shocks` / `evidence_by_key`) but `vector_retriever.retrieve_news`
still returns a flat `{"news": [...]}` list. `_merge` does `{**a, **b}`, which
raises `TypeError: 'list' object is not a mapping` the moment two branches
(or even one) write into the `news` channel — so the news branch cannot run
at all today.

`LagMatrixContext.news_client` exists precisely so a fake can be substituted
here instead of hitting the real Alpaca API. Since `vector_retriever` does
not yet read that field (it builds its own `NewsClient` from `os.environ`
inside `make_retrieve_news`), the fake is also patched over the module's
`NewsClient` import so no test run dials out regardless of which path the
current or future implementation takes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes import vector_retriever as vector_retriever_module
from lagmatrix.graph.state import candidate_key

# topk=3 so CAND (LEAD1/LEAD2 bloc) and CAND2 (LEAD4/LEAD5 bloc) have disjoint
# neighbourhoods and both reach context_fusion/assessor — see test_fanout.py.
TOPK = 3


def _candidate(sym, d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def _article(article_id: str, symbol: str) -> SimpleNamespace:
    """Stands in for one `alpaca.data.models.news.News` item -- only the
    attributes `retrieve_news` actually reads."""
    return SimpleNamespace(
        id=article_id,
        symbols=[symbol],
        headline=f"{symbol} headline {article_id}",
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    )


class FakeNewsClient:
    """Stands in for `alpaca.data.historical.news.NewsClient`. Filters by
    `request.symbols` the same way the real client's response would be
    scoped to the requested symbol, so each candidate's fetch only ever
    returns its own articles."""

    def __init__(self, articles_by_symbol: dict[str, list[SimpleNamespace]]):
        self._articles_by_symbol = articles_by_symbol

    def get_news(self, request):
        return SimpleNamespace(data={"news": self._articles_by_symbol.get(request.symbols, [])})


@pytest.fixture
def fake_news_client(monkeypatch):
    """One article per symbol. Wired onto `LagMatrixContext.news_client`
    (the reserved injection point) and also patched over the `NewsClient`
    import inside `vector_retriever` (what the current, un-fixed factory
    actually constructs) so neither today's nor a `context`-reading fix can
    reach the network in this test.
    """
    client = FakeNewsClient({"CAND": [_article("n1", "CAND")], "CAND2": [_article("n2", "CAND2")]})
    monkeypatch.setattr(vector_retriever_module, "NewsClient", lambda *a, **k: client)
    return client


async def _invoke(closes, candidates, news_client):
    g = build_graph(with_news=True)
    ctx = LagMatrixContext(
        closes=closes, signal_universe=set(), topk=TOPK, news_client=news_client
    )
    return await g.ainvoke({"candidates": candidates}, context=ctx)


async def test_news_branch_runs_with_injected_client(closes, fake_news_client):
    """The graph must complete `with_news=True` using the injected fake and
    produce an assessment.

    Falsifies if: `g.invoke` raises (today: `TypeError: 'list' object is not
    a mapping` from `_merge` in `graph/state.py`, because `retrieve_news`
    returns a flat list for the dict-typed `news` channel), or the run
    completes but `out["assessments"]` is empty.
    """
    out = await _invoke(closes, [_candidate("CAND")], fake_news_client)
    assert out["assessments"]


async def test_news_is_keyed_per_candidate_not_cross_attributed(closes, fake_news_client):
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
    out = await _invoke(closes, [cand, cand2], fake_news_client)

    news = out["news"]
    cand_chunks = news[candidate_key(cand)]
    cand2_chunks = news[candidate_key(cand2)]

    assert {n.symbol for n in cand_chunks} == {"CAND"}
    assert {n.symbol for n in cand2_chunks} == {"CAND2"}
    assert len(cand_chunks) == 1
    assert len(cand2_chunks) == 1
