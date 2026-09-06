"""PHASE-5: `RetryPolicy` + `timeout` on `vector_retriever`, and the deliberate
behaviour change (F-2) that follows from V9 — under `Send` fan-out
`error_handler` never fires (reproduced directly against the installed
`langgraph` below, and pinned as a standing fact by these tests), so the
node's hand-rolled `try/except` is replaced by letting failures propagate: a
transient failure is retried (V8), a slow call times out (V7), and an
exhausted failure halts the whole run rather than silently degrading,
resumable per PHASE-3's checkpointer (V10).

`retrieve_news` must become `async def` for `timeout=` to be legal at all
(V7: a sync node raises `ValueError` at *compile* time) -- these tests
therefore drive the graph with `ainvoke`, per `asyncio_mode = auto` in
pyproject.toml.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from alpaca.common.exceptions import APIError
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeTimeoutError
from langgraph.graph import END, START, StateGraph

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.vector_retriever import retrieve_news
from lagmatrix.graph.state import LagMatrixState, candidate_key

# topk=3 so CAND/CAND2/CANDD land in disjoint blocs -- see test_fanout.py.
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


class FlakyNewsClient:
    """Raises on its first two calls, then succeeds on the third.

    Raises `alpaca.common.exceptions.APIError`, not e.g. `RuntimeError` --
    `RetryPolicy`'s `default_retry_on` returns False for `RuntimeError` (and
    `ValueError`/`OSError`/`TypeError`/`LookupError`), so a fake raising one
    of those would observe zero retries and the test would pass without
    proving the policy retries anything (V8). `APIError` subclasses plain
    `Exception` and so *is* retried.
    """

    def __init__(self):
        self.calls = 0

    def get_news(self, request):
        self.calls += 1
        if self.calls < 3:
            raise APIError(f"simulated transient failure, attempt {self.calls}")
        return SimpleNamespace(data={"news": [_article("n1", request.symbols)]})


async def test_transient_failure_is_retried(closes):
    """A news client that fails twice then succeeds must still complete the
    run, with `RetryPolicy` driving all three attempts.

    Falsifies if: `client.calls != 3` -- today's bare `except Exception` in
    `retrieve_news` swallows the first `APIError`, appends it to `errors`,
    and never calls `get_news` again, so `client.calls == 1`.
    """
    client = FlakyNewsClient()
    graph = build_graph(with_news=True)
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK, news_client=client)
    out = await graph.ainvoke({"candidates": [_candidate("CAND")]}, context=ctx)

    assert out["assessments"], "run should still succeed once the retried call succeeds"
    assert client.calls == 3


class SlowNewsClient:
    """Sleeps past the configured node timeout on every call."""

    def __init__(self, delay: float):
        self._delay = delay

    def get_news(self, request):
        time.sleep(self._delay)
        return SimpleNamespace(data={"news": []})


def _single_node_news_graph(timeout: float):
    """A minimal one-node graph around the real `retrieve_news`, wired with a
    short `timeout`.

    `build_graph`'s own `vector_retriever` node is wired with the fixed
    `timeout=30.0` from TASK-5.5 -- sleeping past that in a test would make
    it slow for no benefit. This harness exercises the same public node
    function through the same langgraph timeout mechanism, at a timeout
    short enough to keep the test fast, rather than reaching into
    `build_graph`'s topology.
    """
    g = StateGraph(LagMatrixState, context_schema=LagMatrixContext)
    g.add_node("vector_retriever", retrieve_news, timeout=timeout)
    g.add_edge(START, "vector_retriever")
    g.add_edge("vector_retriever", END)
    return g.compile()


async def test_timeout_fires_on_a_slow_news_call():
    """A news call slower than the node's `timeout` must raise
    `NodeTimeoutError`, not merely run slow.

    Falsifies if: no `NodeTimeoutError` is raised -- today `retrieve_news` is
    a sync function with no `timeout=` wired at all (and V7 means wiring one
    onto a sync node is rejected at *compile* time with `ValueError` rather
    than enforcing it), so the slow call simply runs to completion.
    """
    client = SlowNewsClient(delay=0.2)
    graph = _single_node_news_graph(timeout=0.05)
    ctx = LagMatrixContext(closes=pd.DataFrame(), signal_universe=set(), news_client=client)

    with pytest.raises(NodeTimeoutError):
        await graph.ainvoke({"candidate": _candidate("CAND")}, context=ctx)


class PermanentlyFailingNewsClient:
    """Fails every call for one symbol until `should_fail` is cleared;
    succeeds immediately for every other symbol. Raises `APIError` for the
    same V8 reason as `FlakyNewsClient`."""

    def __init__(self, fail_symbol: str):
        self._fail_symbol = fail_symbol
        self.should_fail = True
        self.calls: list[str] = []

    def get_news(self, request):
        self.calls.append(request.symbols)
        if self.should_fail and request.symbols == self._fail_symbol:
            raise APIError(f"permanent failure for {request.symbols}")
        return SimpleNamespace(data={"news": [_article("n", request.symbols)]})


async def test_exhausted_news_failure_halts_and_resumes(closes):
    """This is the test that pins PHASE-5's deliberate behaviour change
    (F-2/V9): once `error_handler` cannot preserve the degrade-and-continue
    behaviour under `Send` fan-out (two-or-more concurrent tasks make
    `error_handler` fire but the exception still propagates -- reproduced
    directly against the installed langgraph before writing this test), an
    exhausted news failure must halt the whole run instead of silently
    degrading -- and PHASE-3's checkpointer (V10) must resume it,
    re-executing only the failed branch.

    Falsifies if: the first `ainvoke` does not raise (today's bare
    `except Exception` degrades CAND2's evidence silently and the run
    completes normally) -- or, after the fake is repaired, the resumed
    `ainvoke` re-calls `get_news` for CAND or CANDD (only CAND2's branch may
    re-run), or the final state is missing any candidate's news.
    """
    candidates = [_candidate("CAND"), _candidate("CAND2"), _candidate("CANDD", direction="down")]
    fail_symbol = "CAND2"
    client = PermanentlyFailingNewsClient(fail_symbol)

    graph = build_graph(with_news=True, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK, news_client=client)
    config = {"configurable": {"thread_id": "news-resume-test"}}

    with pytest.raises(APIError):
        await graph.ainvoke({"candidates": candidates}, context=ctx, config=config)

    values = graph.get_state(config).values
    news_by_key = values.get("news", {})
    assert candidate_key(candidates[0]) in news_by_key, "CAND's branch should have succeeded"
    assert candidate_key(candidates[2]) in news_by_key, "CANDD's branch should have succeeded"
    assert candidate_key(candidates[1]) not in news_by_key, "CAND2's branch should not have written"

    calls_before_resume = list(client.calls)
    assert calls_before_resume.count("CAND") == 1
    assert calls_before_resume.count("CANDD") == 1

    client.should_fail = False
    out = await graph.ainvoke(None, context=ctx, config=config)

    new_calls = client.calls[len(calls_before_resume) :]
    assert new_calls == [fail_symbol], "resume must re-run only the previously-failed branch"
    assert {candidate_key(c) for c in candidates} <= out["news"].keys()
