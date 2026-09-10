"""Unstructured half of GraphRAG: news about the candidate itself.

Depends on nothing from the graph half — the query is built from `c.symbol`
alone (D-83 dropped both `direction` and the leader list from it), so this node
needs only the candidate, never its neighbourhood. The previous wording here
said "and its leaders", which stopped being true at D-83 and made the node look
as though it had to run after `graph_retriever`.

Point-in-time — only articles published strictly before the candidate's date are
retrieved, so an assessment never reads news the trader could not have seen.
"""

from __future__ import annotations

import asyncio

from langgraph.runtime import Runtime

from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState, candidate_key


async def retrieve_news(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    """Fetch news for this branch's one candidate.

    `async def` because `timeout=` on a node is rejected at compile time
    otherwise (V7). `NewsIndex.search` is sync and exposes no timeout of its
    own, so it runs in a thread via `asyncio.to_thread`; the node's `timeout=`
    (TASK-5.5) caps the graph's wall clock but cannot cancel the underlying
    ArangoDB call — the thread keeps running until `search` returns.

    No `try/except` here (F-2): a failure propagates and halts the run.
    Under PHASE-2's `Send` fan-out, `error_handler=` fires but no longer
    suppresses the exception once two or more tasks share a superstep (V9),
    so it cannot replace this. PHASE-3's checkpointer makes the halt
    resumable — `ainvoke(None, config)` re-executes only the failed branch
    (V10) — which is the correct behaviour for a batch job producing a
    defensible judgment: silent degradation on missing evidence is the bug.
    """
    index = runtime.context.vector_index
    limit = runtime.context.news_limit

    c = state["candidate"]
    # Direction is deliberately excluded (D-83, docs/spikes/overall.md): news is
    # not written directionally, so a directional query retrieves speculation
    # about direction rather than company news. Substantive terms below
    # replaced a near-contentless query that landed on boilerplate market-wrap
    # headlines shared by every candidate.
    query = f"{c.symbol} catalyst: earnings, demand, guidance, production, regulation"
    chunks = await asyncio.to_thread(index.search, query, [c.symbol], limit, c.as_of)

    return {"news": {candidate_key(c): chunks}, "candidate": c}
