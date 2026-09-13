"""Builds and compiles the LagMatrix StateGraph.

Topology:

    START -(Send x N candidates)-> graph_retriever -+-> (no neighbourhood) -> END
                                                     |
                                    (per-branch conditional, Send)
                                                     |
                                     +-> leader_state -----------------------+
                                     |   vector_retriever -> context_fusion -+
                                     |   quant_perspective -> quant_analyst -+-> assessor
                                     +-> day_trade_perspective -> day_trade_analyst -+
                                                                                     |
                                                                                     v
                                                              review -> publisher -> END

Candidates fan out via `Send` (REQ-1) rather than a runner-side loop.
`leader_state` and `vector_retriever` fan out in parallel once a given
candidate's neighbourhood is known and rejoin at `context_fusion` — that join
is the GraphRAG fusion, running once on the merged state of every candidate
that reached it. `quant_perspective`/`day_trade_perspective` (PHASE-6) fan out
the same way and rejoin directly at `assessor` via their own gather nodes
(`quant_analyst`/`day_trade_analyst`), not through `context_fusion` — neither
perspective is independence-weighted evidence, so neither belongs in that
fusion. `review` (PHASE-6) gates on a contradicted verdict via `interrupt()`,
reading the assessments `assessor` already committed.

`Command` never appears as a node return value here (REQ-8); no subgraph and
no `defer=True` either (D-35 declined all three). `Command` is used elsewhere
(`tests/test_review.py`) but only as the resume input passed to `ainvoke`
after an `interrupt()` — never as something a node returns — so it is not the
primitive D-35 declined.

The graph is mode-agnostic. Extending to market scanning swaps the
CandidateSource behind `ingest` (D-23); no node branches on mode.
"""

from __future__ import annotations

from langgraph.cache.base import BaseCache
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy, RetryPolicy, Send

from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.assessor import assess
from lagmatrix.graph.nodes.context_fusion import fuse_evidence
from lagmatrix.graph.nodes.day_trade_perspective import analyse_day_trade, day_trade_perspective
from lagmatrix.graph.nodes.graph_retriever import retrieve_neighbourhood
from lagmatrix.graph.nodes.leader_state import leader_state
from lagmatrix.graph.nodes.publisher import publish
from lagmatrix.graph.nodes.quant_perspective import analyse_quant, quant_perspective
from lagmatrix.graph.nodes.review import review
from lagmatrix.graph.nodes.vector_retriever import retrieve_news
from lagmatrix.graph.state import LagMatrixState, candidate_key


def fan_out_candidates(state: LagMatrixState) -> list[Send]:
    """One `graph_retriever` branch per candidate (REQ-1)."""
    return [Send("graph_retriever", {"candidate": c}) for c in state.get("candidates", [])]


def build_graph(*, with_news: bool = True, checkpointer=None, cache: BaseCache | None = None):
    """Wire the nodes and compile.

    `closes` and other run-scoped dependencies are supplied at invoke time via
    `LagMatrixContext` (see `graph/context.py`), not at build time.

    `checkpointer` defaults to `None`, which keeps every pre-PHASE-3 test
    compiling bare (V10 needs one supplied to get resumability).

    `cache` (PHASE-4, D-35 item 4) is `None` by default for the same reason.
    `graph_retriever` is cached with the default `key_func`, which pickles the
    `Send` arg — the `Candidate` — but not `closes`. That means the cache key
    does not know which bar frame produced the cached neighbourhood: a graph
    compiled once and invoked across two different `closes` frames would serve
    a stale neighbourhood from the first frame. `runner.py` builds a fresh
    `InMemoryCache()` in the same scope as the `closes` frame it fetches, so
    cache lifetime equals frame lifetime; the `ttl=3600` below is belt-and-
    braces on top of that, not the primary defence.
    """
    g = StateGraph(LagMatrixState, context_schema=LagMatrixContext)
    g.add_node("graph_retriever", retrieve_neighbourhood, cache_policy=CachePolicy(ttl=3600))
    g.add_node("leader_state", leader_state)
    g.add_node("quant_perspective", quant_perspective)
    g.add_node(
        "quant_analyst", analyse_quant,
        retry_policy=RetryPolicy(max_attempts=3),
    )
    g.add_node("day_trade_perspective", day_trade_perspective)
    g.add_node(
        "day_trade_analyst", analyse_day_trade,
        retry_policy=RetryPolicy(max_attempts=3),
    )
    g.add_node("context_fusion", fuse_evidence)
    g.add_node("assessor", assess)
    g.add_node("review", review)
    g.add_node("publisher", publish)

    fan_out = ["leader_state", "quant_perspective", "day_trade_perspective"]
    if with_news:
        g.add_node(
            "vector_retriever", retrieve_news,
            retry_policy=RetryPolicy(max_attempts=3), timeout=30.0,
        )
        g.add_edge("vector_retriever", "context_fusion")
        fan_out.append("vector_retriever")

    def route_on_neighbourhood(state: LagMatrixState) -> list[Send] | str:
        """Assess only this branch's own candidate if it has a neighbourhood.

        Reads the branch's own just-written `lag_edges` (V3 — isolated from
        sibling branches at this point), so this is a per-candidate D-27
        short-circuit, not a batch-wide one.
        """
        if not state.get("lag_edges"):
            return END
        c = state["candidate"]
        key = candidate_key(c)
        edges = state.get("lag_edges_by_key", {}).get(key, [])
        arg = {"candidate": c, "lag_edges_by_key": {key: edges}}
        return [Send(name, arg) for name in fan_out]

    g.add_conditional_edges(START, fan_out_candidates, ["graph_retriever"])
    g.add_conditional_edges("graph_retriever", route_on_neighbourhood, [*fan_out, END])
    g.add_edge("leader_state", "context_fusion")
    g.add_edge("quant_perspective", "quant_analyst")
    g.add_edge("day_trade_perspective", "day_trade_analyst")

    g.add_edge("context_fusion", "assessor")
    g.add_edge("quant_analyst", "assessor")
    g.add_edge("day_trade_analyst", "assessor")
    g.add_edge("assessor", "review")
    g.add_edge("review", "publisher")
    g.add_edge("publisher", END)
    return g.compile(checkpointer=checkpointer, cache=cache)
