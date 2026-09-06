"""Unstructured half of GraphRAG: news co-mentioning the candidate and its leaders.

Point-in-time — only articles published strictly before the candidate's date are
retrieved, so an assessment never reads news the trader could not have seen.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, time, timedelta

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from langgraph.runtime import Runtime

from lagmatrix.domain.models import NewsChunk
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState, candidate_key


async def retrieve_news(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    """Fetch news for this branch's one candidate.

    `async def` because `timeout=` on a node is rejected at compile time
    otherwise (V7). `NewsClient.get_news` is sync and exposes no timeout of
    its own, so it runs in a thread via `asyncio.to_thread`; the node's
    `timeout=` (TASK-5.5) caps the graph's wall clock but cannot cancel the
    underlying socket — the thread keeps running until `get_news` returns.

    No `try/except` here (F-2): a failure propagates and halts the run.
    Under PHASE-2's `Send` fan-out, `error_handler=` fires but no longer
    suppresses the exception once two or more tasks share a superstep (V9),
    so it cannot replace this. PHASE-3's checkpointer makes the halt
    resumable — `ainvoke(None, config)` re-executes only the failed branch
    (V10) — which is the correct behaviour for a batch job producing a
    defensible judgment: silent degradation on missing evidence is the bug.
    """
    client = runtime.context.news_client
    if client is None:
        client = NewsClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    lookback_days = runtime.context.news_lookback_days
    limit = runtime.context.news_limit

    c = state["candidate"]
    end = datetime.combine(c.as_of, time.min, tzinfo=UTC)
    res = await asyncio.to_thread(
        client.get_news,
        NewsRequest(
            symbols=c.symbol,
            start=end - timedelta(days=lookback_days),
            end=end,
            limit=limit,
            include_content=False,
        ),
    )
    chunks = [
        NewsChunk(
            doc_id=str(n.id),
            symbol=c.symbol,
            text=n.headline,
            published_at=n.created_at,
            score=float(len(n.symbols)),  # breadth: fewer symbols = more specific
        )
        for n in res.data.get("news", [])
    ]

    return {"news": {candidate_key(c): chunks}, "candidate": c}
