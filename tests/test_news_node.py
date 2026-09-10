"""PHASE-5: `RetryPolicy` + `timeout` on `vector_retriever`, and the deliberate
behaviour change (F-2) that follows from V9 — under `Send` fan-out
`error_handler` never fires (reproduced directly against the installed
`langgraph` below, and pinned as a standing fact by these tests), so the
node's hand-rolled `try/except` is replaced by letting failures propagate: a
transient failure is retried (V8), a slow call times out (V7), and an
exhausted failure halts the whole run rather than silently degrading,
resumable per PHASE-3's checkpointer (V10).

PHASE-7 retargets all three fakes below from the old direct Alpaca
`NewsClient.get_news(request)` call to the real semantic index's
`NewsIndex.search(query, symbols, limit)` (`src/lagmatrix/adapters/vector.py`)
— same three guarantees (retry, timeout, halt-and-resume), same falsifiers,
new collaborator. It also adds one new test pinning the query the node builds
and the real similarity score `NewsIndex.search` returns.

`retrieve_news` must become `async def` for `timeout=` to be legal at all
(V7: a sync node raises `ValueError` at *compile* time) -- these tests
therefore drive the graph with `ainvoke`, per `asyncio_mode = auto` in
pyproject.toml.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from arango.exceptions import AQLQueryExecuteError
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeTimeoutError
from langgraph.graph import END, START, StateGraph

from lagmatrix.domain.models import Candidate, NewsChunk
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.vector_retriever import retrieve_news
from lagmatrix.graph.state import LagMatrixState, candidate_key

# topk=3 so CAND/CAND2/CANDD land in disjoint blocs -- see test_fanout.py.
TOPK = 3


def _candidate(sym, d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def _chunk(doc_id: str, symbol: str, score: float = 1.0) -> NewsChunk:
    """Stands in for one row `NewsIndex.search` maps into a `NewsChunk` --
    same shape `retrieve_news` is expected to receive back."""
    return NewsChunk(
        doc_id=doc_id,
        symbol=symbol,
        text=f"{symbol} headline {doc_id}",
        published_at=datetime(2026, 5, 30, tzinfo=UTC),
        score=score,
    )


class SimulatedAQLError(AQLQueryExecuteError):
    """Constructible stand-in for what `db.aql.execute` raises.

    `ArangoServerError.__init__` requires a real `Response` and `Request`
    (`AQLQueryExecuteError(msg)` raises `TypeError: missing 1 required
    positional argument: 'request'`) -- a test has neither. Subclassing and
    bypassing the parent `__init__` keeps the *type* the retry decision turns
    on -- `isinstance(e, AQLQueryExecuteError)` is still `True` and
    `RetryPolicy.default_retry_on` still returns `True` -- without
    fabricating a fake HTTP exchange. Do not "simplify" this back to
    `raise AQLQueryExecuteError(msg)`; that reintroduces the `TypeError`
    inside `search()`, before `retrieve_news` or `RetryPolicy` ever see it.
    """

    def __init__(self, msg: str) -> None:
        Exception.__init__(self, msg)


class FlakyNewsIndex:
    """Raises on its first two `.search()` calls, then succeeds on the third.

    Raises `SimulatedAQLError`, a constructible stand-in for
    `arango.exceptions.AQLQueryExecuteError` -- the exception
    `NewsIndex.search` would actually propagate from `self.db.aql.execute(...)`
    on a transient server-side query failure. Checked against the installed
    `langgraph`'s `RetryPolicy.default_retry_on`: it explicitly excludes
    `ValueError`/`TypeError`/`ArithmeticError`/`ImportError`/`LookupError`/
    `NameError`/`SyntaxError`/`RuntimeError`/`ReferenceError`/`StopIteration`/
    `StopAsyncIteration`/`OSError` (returns `False`, i.e. "do not retry" for
    each), then falls through to `return True` for anything else.
    `AQLQueryExecuteError`'s MRO is `AQLQueryExecuteError -> ArangoServerError
    -> ArangoError -> Exception` -- none of those excluded types -- so it
    reaches the fallthrough and *is* retried. A fake raising one of the
    excluded types instead would observe zero retries and the test would pass
    without proving the policy retries anything (V8).
    """

    def __init__(self):
        self.calls = 0

    def search(self, query, symbols, limit, as_of=None):
        self.calls += 1
        if self.calls < 3:
            raise SimulatedAQLError(f"simulated transient failure, attempt {self.calls}")
        return [_chunk("n1", symbols[0])]


async def test_transient_failure_is_retried(closes):
    """A news index that fails twice then succeeds must still complete the
    run, with `RetryPolicy` driving all three attempts.

    Falsifies if: `index.calls != 3` -- a bare `except Exception` around
    `.search()` in `retrieve_news` would swallow the first
    `AQLQueryExecuteError`, append it to `errors`, and never call `.search()`
    again, so `index.calls == 1`.
    """
    index = FlakyNewsIndex()
    graph = build_graph(with_news=True)
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK, vector_index=index)
    out = await graph.ainvoke({"candidates": [_candidate("CAND")]}, context=ctx)

    assert out["assessments"], "run should still succeed once the retried call succeeds"
    assert index.calls == 3


class SlowNewsIndex:
    """Sleeps past the configured node timeout on every `.search()` call."""

    def __init__(self, delay: float):
        self._delay = delay

    def search(self, query, symbols, limit, as_of=None):
        time.sleep(self._delay)
        return []


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
    index = SlowNewsIndex(delay=0.2)
    graph = _single_node_news_graph(timeout=0.05)
    ctx = LagMatrixContext(closes=pd.DataFrame(), signal_universe=set(), vector_index=index)

    with pytest.raises(NodeTimeoutError):
        await graph.ainvoke({"candidate": _candidate("CAND")}, context=ctx)


class PermanentlyFailingNewsIndex:
    """Fails every `.search()` call for one symbol until `should_fail` is
    cleared; succeeds immediately for every other symbol. Raises
    `SimulatedAQLError` for the same V8 reason as `FlakyNewsIndex`."""

    def __init__(self, fail_symbol: str):
        self._fail_symbol = fail_symbol
        self.should_fail = True
        self.calls: list[str] = []

    def search(self, query, symbols, limit, as_of=None):
        symbol = symbols[0]
        self.calls.append(symbol)
        if self.should_fail and symbol == self._fail_symbol:
            raise SimulatedAQLError(f"permanent failure for {symbol}")
        return [_chunk("n", symbol)]


async def test_exhausted_news_failure_halts_and_resumes(closes):
    """This is the test that pins PHASE-5's deliberate behaviour change
    (F-2/V9): once `error_handler` cannot preserve the degrade-and-continue
    behaviour under `Send` fan-out (two-or-more concurrent tasks make
    `error_handler` fire but the exception still propagates -- reproduced
    directly against the installed langgraph before writing this test), an
    exhausted news failure must halt the whole run instead of silently
    degrading -- and PHASE-3's checkpointer (V10) must resume it,
    re-executing only the failed branch.

    Falsifies if: the first `ainvoke` does not raise (a bare `except
    Exception` degrading CAND2's evidence silently and the run completing
    normally) -- or, after the fake is repaired, the resumed `ainvoke`
    re-calls `.search()` for CAND or CANDD (only CAND2's branch may re-run),
    or the final state is missing any candidate's news.
    """
    candidates = [_candidate("CAND"), _candidate("CAND2"), _candidate("CANDD", direction="down")]
    fail_symbol = "CAND2"
    index = PermanentlyFailingNewsIndex(fail_symbol)

    graph = build_graph(with_news=True, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK, vector_index=index)
    config = {"configurable": {"thread_id": "news-resume-test"}}

    with pytest.raises(AQLQueryExecuteError):
        await graph.ainvoke({"candidates": candidates}, context=ctx, config=config)

    values = graph.get_state(config).values
    news_by_key = values.get("news", {})
    assert candidate_key(candidates[0]) in news_by_key, "CAND's branch should have succeeded"
    assert candidate_key(candidates[2]) in news_by_key, "CANDD's branch should have succeeded"
    assert candidate_key(candidates[1]) not in news_by_key, "CAND2's branch should not have written"

    calls_before_resume = list(index.calls)
    assert calls_before_resume.count("CAND") == 1
    assert calls_before_resume.count("CANDD") == 1

    index.should_fail = False
    out = await graph.ainvoke(None, context=ctx, config=config)

    new_calls = index.calls[len(calls_before_resume) :]
    assert new_calls == [fail_symbol], "resume must re-run only the previously-failed branch"
    assert {candidate_key(c) for c in candidates} <= out["news"].keys()


class CapturingNewsIndex:
    """Records every `.search()` call's arguments; returns one canned chunk
    carrying a real similarity-style score (0.42 -- deliberately not equal to
    `len(symbols)` for a single-symbol query, so a node still computing
    breadth instead of forwarding the index's own score cannot pass by
    coincidence)."""

    def __init__(self):
        self.calls: list[dict] = []

    def search(self, query, symbols, limit, as_of=None):
        self.calls.append({"query": query, "symbols": symbols, "limit": limit, "as_of": as_of})
        return [_chunk("n1", symbols[0], score=0.42)]


async def test_retrieve_news_derives_query_from_the_candidate(closes):
    """`retrieve_news` must build its `.search()` query from the branch's own
    candidate -- the candidate's own `symbol`, plus substantive content terms
    that make it a real question about the company rather than a sentiment
    prompt -- and must pass `symbols`/`limit` sensibly, not generically.

    A prior version of this spec required only the symbol and direction in
    the query (`f"news relevant to a {direction} move in {symbol}"`). Probing
    the live index against that query showed every retrieved article for
    three separate candidates was the same recurring boilerplate headline
    ("Market-Moving News for <date>") -- a near-contentless query lands on
    the corpus centroid, where generic filler lives. Swapping in substantive
    terms (below) retrieved company-specific headlines instead.

    Falsifies if: the captured `query` does not mention the candidate's own
    `symbol` -- e.g. a hardcoded `"latest news"` string, or a query built
    from some other candidate's fields, would still let the run complete but
    fail this assertion. Also falsifies if the query regresses to the
    contentless sentiment prompt above, which is missing the substantive
    terms asserted below. Also falsifies if `symbols` is not scoped to this
    candidate alone (e.g. the whole universe) or `limit` is not
    `runtime.context.news_limit`.
    """
    index = CapturingNewsIndex()
    graph = build_graph(with_news=True)
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK, vector_index=index)
    candidate = _candidate("CAND", direction="up")
    out = await graph.ainvoke({"candidates": [candidate]}, context=ctx)

    assert index.calls, "search() must have been called"
    call = index.calls[0]
    assert candidate.symbol in call["query"]
    for term in ("earnings", "demand", "guidance", "production", "regulation"):
        assert term in call["query"], f"query missing substantive content term {term!r}"
    assert call["symbols"] == [candidate.symbol]
    assert call["limit"] == ctx.news_limit

    # The real similarity score from the index must survive into the node's
    # output unchanged -- not recomputed as `len(n.symbols)` breadth (which,
    # for this single-symbol chunk, would silently also equal a plausible
    # score and hide the bug; 0.42 cannot be produced that way).
    chunks = out["news"][candidate_key(candidate)]
    assert chunks[0].score == 0.42


@pytest.mark.parametrize("direction", ["up", "down"])
async def test_retrieve_news_query_excludes_direction(closes, direction):
    """The query sent to `.search()` must NOT mention the candidate's
    `direction` -- this inverts the original spec, which required it.

    Probing the live index showed *adding* direction back in (e.g. `f"{symbol}
    catalyst for a {direction} move: earnings, demand, ..."`) makes retrieval
    worse, not better: it pulled in options-flow and trade-idea chatter
    ("Smart Money Is Betting Big In ... Options", "Trade Strategy For SPY,
    QQQ, ...") -- the same boilerplate-magnet pathology as the original
    contentless query, reintroduced by a different route. News is not
    written directionally; asking for it directionally retrieves people
    speculating about direction, not company news. Direction belongs to the
    thesis being assessed downstream, not to what context is retrieved.

    Falsifies if: the captured query contains `"up"` or `"down"` -- e.g. a
    query built as `f"{c.symbol} catalyst for a {c.direction} move: ..."`
    would pass every other assertion in this file yet still reintroduce the
    regression this test exists to catch.
    """
    index = CapturingNewsIndex()
    graph = build_graph(with_news=True)
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK, vector_index=index)
    candidate = _candidate("CAND", direction=direction)
    await graph.ainvoke({"candidates": [candidate]}, context=ctx)

    assert index.calls, "search() must have been called"
    query = index.calls[0]["query"]
    assert "up" not in query
    assert "down" not in query


async def test_retrieve_news_passes_the_candidates_own_as_of_to_search(closes):
    """`retrieve_news` must forward this branch's own candidate `as_of` to
    `.search()`, so the index can bound retrieval to what existed by then
    (point-in-time correctness -- a retrieval that leaks later news into an
    earlier assessment is the bug this pins).

    Falsifies if: the captured `as_of` is `None` (the node never passes it --
    `CapturingNewsIndex.search`'s `as_of=None` default records exactly this
    when the argument is omitted, which is what today's `retrieve_news`
    does), today's date, or some other candidate's `as_of`.
    """
    index = CapturingNewsIndex()
    graph = build_graph(with_news=True)
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK, vector_index=index)
    candidate = _candidate("CAND", direction="up")
    await graph.ainvoke({"candidates": [candidate]}, context=ctx)

    assert index.calls, "search() must have been called"
    assert index.calls[0]["as_of"] == candidate.as_of
