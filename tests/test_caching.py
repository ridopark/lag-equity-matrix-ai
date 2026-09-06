"""PHASE-4: `CachePolicy` on `graph_retriever` (D-35 item 4 / REQ-4).

**Read F-1 in the plan before trusting the second test's expectations.** V12
(probed directly against the installed `langgraph 1.2.11`, see the RED-phase
report) established that `InMemoryCache` does **not** dedupe concurrent `Send`
tasks landing in the same superstep — it only hits on a *later* `invoke`. That
is narrower than spike 11's original justification for this item (a
per-candidate loop that no longer exists after PHASE-2), and F-1 records the
gap. These two tests pin both halves of that observed behaviour: caching does
save repeat work *across* invocations, and it does *not* save anything for
duplicate candidates fanned out *within* one invocation.
"""

from __future__ import annotations

from datetime import date

from langgraph.cache.memory import InMemoryCache

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.graph_retriever import (
    retrieve_neighbourhood as real_retrieve_neighbourhood,
)


def _candidate(sym, d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


def _spy(calls):
    """Wraps the real node body, recording every call before delegating."""

    def spy(state, runtime):
        calls.append(state["candidate"].symbol)
        return real_retrieve_neighbourhood(state, runtime)

    return spy


def test_neighbourhood_is_recomputed_only_once_across_invocations(closes, monkeypatch):
    """A second `invoke` over the same candidates, on a different `thread_id`,
    must not re-run `retrieve_neighbourhood`'s body at all — `InMemoryCache`
    hits on the identical `Candidate` Send arg from the first invoke.

    Falsifies if: the second invoke's call count is anything other than 0 —
    e.g. because `build_graph` never wires `cache=`/`cache_policy=` onto the
    node, or because the cache key does not actually match across invokes.
    """
    calls: list[str] = []
    monkeypatch.setattr("lagmatrix.graph.builder.retrieve_neighbourhood", _spy(calls))

    graph = build_graph(with_news=False, cache=InMemoryCache())
    ctx = LagMatrixContext(closes=closes, signal_universe=set())
    candidates = [_candidate("CAND"), _candidate("CAND2"), _candidate("CANDD", direction="down")]

    graph.invoke(
        {"candidates": candidates}, context=ctx,
        config={"configurable": {"thread_id": "cache-first"}},
    )
    assert len(calls) == 3, "first invoke should run the body once per candidate"

    calls.clear()
    graph.invoke(
        {"candidates": candidates}, context=ctx,
        config={"configurable": {"thread_id": "cache-second"}},
    )
    assert calls == [], "second invoke should hit the cache for every candidate"


def test_cache_does_not_dedupe_within_one_invocation(closes, monkeypatch):
    """Pins V12/F-1 as an observed limitation, not an aspiration: within a
    single invocation, `Send` fans the same candidate out to two concurrent
    tasks in one superstep, and `InMemoryCache` does not dedupe between them
    — both run the node body. A future reader must not assume the mere
    presence of `cache_policy=` means duplicate candidates in one batch are
    computed once; they are not.

    Falsifies if: the body runs only once for the duplicated candidate — that
    would mean the installed `langgraph` version's caching semantics changed
    to dedupe within a superstep, not that this repo's code broke.
    """
    calls: list[str] = []
    monkeypatch.setattr("lagmatrix.graph.builder.retrieve_neighbourhood", _spy(calls))

    graph = build_graph(with_news=False, cache=InMemoryCache())
    ctx = LagMatrixContext(closes=closes, signal_universe=set())
    cand = _candidate("CAND")

    graph.invoke(
        {"candidates": [cand, cand]}, context=ctx,
        config={"configurable": {"thread_id": "dup-within-one"}},
    )
    assert len(calls) == 2, "duplicate candidate in one batch must not be deduped by the cache"
