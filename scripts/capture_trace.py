"""Record the graph executing over the candidates, as a replayable trace.

Driven with `astream`: PHASE-5 made `retrieve_news` async, and one async node
makes sync `.stream()`/`.invoke()` fail for the whole graph.

A shared link cannot stream from a local process, so the web view replays a
captured run rather than driving one. The timings are real: `t_ms` is measured
wall-clock from the start of the invocation.

Usage:  uv run python scripts/capture_trace.py [--news] [--limit N]
Output: data/trace.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import time
from datetime import UTC, datetime

import pandas as pd

from lagmatrix.adapters.candidates import ExternalSignals
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import candidate_key


def detail(node: str, upd) -> tuple[str | None, str]:
    """(candidate key, human-readable summary) for one node update."""
    if not isinstance(upd, dict):
        return None, ""
    c = upd.get("candidate")
    key = candidate_key(c) if c is not None else None
    if node == "graph_retriever":
        edges = next(iter(upd.get("lag_edges_by_key", {}).values()), [])
        return key, f"{len(edges)} neighbours"
    if node == "leader_state":
        sh = next(iter(upd.get("leader_shocks", {}).values()), [])
        return key, f"{len(sh)} moved"
    if node == "vector_retriever":
        nw = next(iter(upd.get("news", {}).values()), [])
        return key, f"{len(nw)} articles"
    if node == "context_fusion":
        by = upd.get("effective_evidence_by_key", {})
        return None, f"{len(by)} candidates weighted"
    if node == "assessor":
        a = upd.get("assessments", [])
        return None, f"{len(a)} assessments"
    return key, ""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="data/trace.json")
    args = ap.parse_args()

    bars = pd.read_parquet("data/bars.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    all_c = ExternalSignals().candidates()
    cands = all_c[: args.limit] if args.limit else all_c
    ctx = LagMatrixContext(closes=closes, signal_universe={c.symbol for c in all_c})

    graph = build_graph(with_news=args.news)
    events: list[dict] = []
    t0 = time.perf_counter()

    with contextlib.redirect_stdout(io.StringIO()):
        async for chunk in graph.astream(
            {"candidates": cands}, context=ctx, stream_mode="updates"
        ):
            ms = round((time.perf_counter() - t0) * 1000, 1)
            for node, upd in chunk.items():
                key, txt = detail(node, upd)
                events.append({"t_ms": ms, "node": node, "key": key, "detail": txt})
        final = await graph.ainvoke({"candidates": cands}, context=ctx)

    total = round((time.perf_counter() - t0) * 1000, 1)
    assessments = [
        {
            "symbol": a.candidate.symbol,
            "as_of": a.candidate.as_of.isoformat(),
            "direction": a.candidate.direction,
            "verdict": a.verdict,
            "effective_evidence": round(a.effective_evidence, 3),
            "n_supporting": len(a.supporting),
            "n_contradicting": len(a.contradicting),
        }
        for a in final.get("assessments", [])
    ]

    trace = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "candidates": len(cands),
            "with_news": args.news,
            "universe_symbols": int(closes.shape[1]),
            "sessions": int(closes.shape[0]),
            "total_ms": total,
            "event_count": len(events),
        },
        "events": events,
        "assessments": assessments,
    }
    with open(args.out, "w") as fh:
        json.dump(trace, fh, separators=(",", ":"))

    per_node: dict[str, int] = {}
    for e in events:
        per_node[e["node"]] = per_node.get(e["node"], 0) + 1
    print(f"wrote {args.out}")
    print(f"  candidates {len(cands)} | events {len(events)} | {total:.0f} ms")
    print(f"  per node: {per_node}")
    print(f"  assessments {len(assessments)}")


if __name__ == "__main__":
    asyncio.run(main())
