"""Capture one real GraphRAG run into a self-contained trace for the public page.

The page cannot query ArangoDB — it is on a private homelab — so it replays a
run that genuinely happened. Everything here is measured, not staged: the
traversal is a real AQL query, the retrieval a real ANN search, the timings real
wall-clock, the verdicts the pipeline's own.

Emits docs/showcase-trace.json:
  meta          corpus sizes, model, versions
  candidates    per candidate: the traversed subgraph, retrieved articles, verdict
  events        node-by-node LangGraph timeline with t_ms
  aql           the exact queries issued, so a reader can check the claims

Usage:  uv run python scripts/capture_showcase.py [--out docs/showcase-trace.json]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import pathlib
import re
import socket
import sys
import tempfile
import time
from datetime import date
from urllib.parse import urlparse

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from capture_trace import detail  # noqa: E402

DB = "lagmatrix"
DB_HANDLE = None  # set in main(); the adapters and this script share one handle
EXCLUDED: list[str] = []  # ETF tickers, loaded in main()

# DISTINCT over the whole projection would count one company once per PATH that
# reaches it -- AAPL came back as 71 "1-hop suppliers" for 13 real ones. Collapse
# to the company, then pick ONE representative edge for its facts: MAX() across a
# company's several filings mixes them, and returned Amkor's seasonality
# paragraph instead of its 69%-of-revenue disclosure. Sorting by pct_revenue puts
# the edge that actually discloses a number first (TO_NUMBER(null) is 0), and
# pct/filed/says then all come from that same edge rather than three different ones.
TRAVERSAL_AQL = """
FOR v, e, p IN 1..@hops INBOUND @start supplies_to
  FILTER p.edges[*].filing_date ALL <= @as_of
  COLLECT symbol = v.symbol INTO g
  LET paths = g[*].p
  LET depth = MIN(FOR x IN paths RETURN LENGTH(x.edges))
  // the quoted filing must lie on the path the page draws, so choose among the
  // SHORTEST paths only. Ranking over all paths let a 2-hop filing be shown
  // beside a 1-hop label -- COHR sat at 1 hop from AAPL quoting Qorvo's 10-K.
  LET best = FIRST(FOR x IN paths
                     FILTER LENGTH(x.edges) == depth
                     SORT TO_NUMBER(x.edges[-1].pct_revenue) DESC,
                          x.edges[-1].filing_date DESC
                     LIMIT 1 RETURN x)
  RETURN {
    symbol,
    hops:  depth,
    path:  CONCAT_SEPARATOR(">", REVERSE(best.vertices[*].symbol)),
    filed: best.edges[-1].filing_date,
    says:  best.edges[-1].passage,
    named: best.edges[-1].counterparty
  }
"""

VECTOR_AQL = """
FOR a IN article
  LET score = APPROX_NEAR_COSINE(a.embedding, @vec)
  FILTER a.date < @as_of
  SORT score DESC
  LIMIT @k
  RETURN {headline: a.headline, date: a.date, symbols: a.symbols, score: score}
"""



# The supply graph is only one hop deep once the audit removes the false edges,
# so multi-hop is demonstrated where depth is real: co-mention, PMI-weighted.
# Two ALL-quantified per-path constraints at once -- every edge on the path must
# clear the PMI floor AND have existed by `as_of` -- is the shape a dataframe
# cannot express without materialising the transitive closure first.
COMENTION_AQL = """
FOR v, e, p IN 1..2 ANY @start co_mentioned
  OPTIONS {uniqueVertices: "path", bfs: true}
  FILTER p.edges[*].pmi ALL >= @minpmi
  FILTER p.edges[*].first_seen ALL <= @as_of
  // An ETF is co-mentioned with every constituent, so it is a hub that makes
  // any two holdings look two hops apart. "AVGO and TSM connect via SOXX" is an
  // artifact of the index, not a relationship. Exclude them from the whole path.
  FILTER p.vertices[*].symbol NONE IN @excluded
  COLLECT symbol = v.symbol INTO g
  LET paths = g[*].p
  LET depth = MIN(FOR x IN paths RETURN LENGTH(x.edges))
  FILTER depth == 2
  LET best = FIRST(FOR x IN paths FILTER LENGTH(x.edges) == depth
                     SORT MIN(x.edges[*].pmi) DESC LIMIT 1 RETURN x)
  SORT MIN(best.edges[*].pmi) DESC
  LIMIT 7
  RETURN {symbol, hops: depth,
          path: CONCAT_SEPARATOR(">", best.vertices[*].symbol),
          pmi: MIN(best.edges[*].pmi),
          via: best.vertices[1].symbol,
          articles: MIN(best.edges[*].articles)}
"""

SENT = re.compile(r"(?<!\bInc)(?<!\bCorp)(?<!\bLtd)(?<!\bCo)(?<!\bNo)"
                  r"(?<!\bU\.S)(?<!\s[A-Z])\.\s+(?=[A-Z(\"])")

def disclosure(passage: str, named: str | None) -> tuple[str | None, str | None]:
    """The verbatim sentence that names the counterparty, and its percentage.

    Two things this deliberately does not do. It does not fall back to the
    nearest sentence carrying a percentage: load_edgar.py stores the first
    percentage anywhere in the +/-420-char window, and Amkor's window opens
    with "our ten largest customers accounted for 69%" well before it reaches
    "Direct sales to Apple Inc. accounted for 27.7%" -- so the stored figure
    is not the disclosure. And it does not require a percentage at all. Most
    filings name a customer without sizing the relationship; that sentence is
    still the evidence that created the edge, and the page says so rather
    than dropping the node.
    """
    if not passage or not named:
        return None, None
    key = named.split()[0].strip(",.")
    hit = next((s.strip() for s in SENT.split(passage) if key in s), None)
    if not hit:
        return None, None
    pct = next((m for m in re.finditer(r"(\d{1,3}(?:\.\d)?)\s?%", hit)
                if not re.match(r"\s*or\s+(more|greater|higher)",
                                hit[m.end():], re.I)), None)
    return hit, (pct.group(1) if pct else None)


def connect():
    """python-arango handle, not the old ssh+arangosh shim.

    The adapters take a `StandardDatabase`, and this script now runs the real
    pipeline rather than issuing queries beside it, so it needs the same handle
    they do. That means a tunnel to the homelab:

        ssh -f -N -L 19999:127.0.0.1:19999 ridopark@192.168.10.123
        # with `kubectl -n lagmatrix port-forward deploy/arangodb 19999:8529` on the far side
    """
    from arango import ArangoClient

    url = os.environ.get("LAGMATRIX_ARANGO_URL", "http://localhost:19999")
    pw = pathlib.Path(os.path.expanduser("~/.lagmatrix-arango-pw")).read_text().strip()
    host, port = urlparse(url).hostname, urlparse(url).port or 8529
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except OSError as exc:
        sys.exit(f"no ArangoDB at {url} ({exc}). Open the tunnel; see connect().__doc__")
    return ArangoClient(hosts=url).db(DB, username="root", password=pw)


def query(aql: str, binds: dict) -> tuple[list, float]:
    """Rows plus server-measured wall-clock, for the provenance panels."""
    t0 = time.perf_counter()
    rows = list(DB_HANDLE.aql.execute(aql, bind_vars=binds))
    return rows, round((time.perf_counter() - t0) * 1000, 1)


LEGACY_QUERY = "news relevant to a {direction} move in {symbol}"

FANNED = {"graph_retriever", "leader_state", "vector_retriever"}


async def run_pipeline(seeds: list[str], as_of: str, limit: int) -> tuple:
    """Stream one real `graph.astream` and record what the nodes actually did.

    This used to be a `mark()` helper called by hand around direct AQL queries,
    which meant the page showed a LangGraph timeline that no LangGraph run had
    produced. Everything here is now observed: the `Send` fan-out, the parallel
    `leader_state`/`vector_retriever` branches rejoining at `context_fusion`,
    the checkpointer, the `interrupt()` gate in `review`, and every `t_ms`.
    """
    import aiosqlite
    from langgraph.cache.memory import InMemoryCache
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command

    from lagmatrix.adapters.arango import ArangoTopology
    from lagmatrix.adapters.vector import NewsIndex
    from lagmatrix.domain.models import Candidate
    from lagmatrix.graph.builder import build_graph
    from lagmatrix.graph.context import LagMatrixContext

    bars = pd.read_parquet("data/bars.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    day = date.fromisoformat(as_of)
    cands = [Candidate(symbol=s, direction="up", as_of=day, origin="scan") for s in seeds]

    ctx = LagMatrixContext(
        closes=closes,
        signal_universe={c.symbol for c in cands},
        arango_topology=ArangoTopology(DB_HANDLE),
        vector_index=NewsIndex(DB_HANDLE),
        max_lag_hops=2,
        news_limit=limit,
        # The gate only fires on a contradicted verdict, so this is the setting
        # under which the interrupt is demonstrable at all (D-19).
        halt_on_contradicted=True,
    )

    events: list[dict] = []
    t0 = time.perf_counter()
    events.append({"t_ms": 0.0, "node": "START", "key": None,
                   "detail": f"Send fan-out: {len(cands)} candidates"})

    # A real checkpointer over a throwaway file: the interrupt gate and resume
    # semantics only exist if state is genuinely persisted between supersteps.
    # Same registration the production runner uses; without it every model
    # round-trips through the checkpointer with an "unregistered type" warning.
    from lagmatrix.pipeline.runner import _ALLOWED_MODELS
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODELS)
    with tempfile.TemporaryDirectory() as tmp:
        async with aiosqlite.connect(f"{tmp}/showcase.sqlite") as conn:
            graph = build_graph(with_news=True,
                                checkpointer=AsyncSqliteSaver(conn, serde=serde),
                                cache=InMemoryCache())
            config = {"configurable": {"thread_id": "showcase"}}
            tasks: dict = {}
            ckpts: list = []

            def absorb(mode, data):
                """`debug` carries task start/end and checkpoint ids; `updates`
                carries what each node returned. Recording both is what lets the
                page show real overlapping intervals instead of inferring
                concurrency from two events happening to share a millisecond."""
                now = round((time.perf_counter() - t0) * 1000, 1)
                if mode == "updates":
                    for node, upd in data.items():
                        key, txt = detail(node, upd)
                        events.append({"t_ms": now, "node": node, "key": key,
                                       "detail": txt or ""})
                elif mode == "debug":
                    kind, pay = data.get("type"), data.get("payload", {})
                    if kind == "task":
                        tasks[pay.get("id")] = {
                            "node": pay.get("name"), "step": data.get("step"),
                            "start_ms": now,
                            # only the fanned-out nodes are per-candidate; the
                            # fan-in nodes see merged state and would otherwise
                            # pick up whichever candidate happened to be last
                            "key": ((pay.get("input") or {}).get("candidate")
                                    if pay.get("name") in FANNED else None),
                        }
                    elif kind == "task_result":
                        tk = tasks.get(pay.get("id"))
                        if tk:
                            tk["end_ms"] = now
                    elif kind == "checkpoint":
                        ckpts.append({"t_ms": now, "step": data.get("step"),
                                      "id": (pay.get("config") or {}).get(
                                          "configurable", {}).get("checkpoint_id")})

            with contextlib.redirect_stdout(io.StringIO()):   # publisher prints
                async for mode, data in graph.astream(
                    {"candidates": cands}, context=ctx,
                    config=config, stream_mode=["updates", "debug"],
                ):
                    absorb(mode, data)
                snap = await graph.aget_state(config)
                if snap.interrupts:
                    payload = snap.interrupts[0].value
                    events.append({
                        "t_ms": round((time.perf_counter() - t0) * 1000, 1),
                        "node": "review", "key": None, "interrupt": payload,
                        "detail": "interrupt(): " + ", ".join(
                            c["symbol"] for c in payload["contradicted"]
                        ) + " contradicted — run halted",
                    })
                    async for mode, data in graph.astream(
                        Command(resume=True), context=ctx,
                        config=config, stream_mode=["updates", "debug"],
                    ):
                        absorb(mode, data)
                    snap = await graph.aget_state(config)

    spans = [
        {"node": v["node"], "step": v["step"], "start_ms": v["start_ms"],
         "end_ms": v.get("end_ms", v["start_ms"]),
         "key": getattr(v.get("key"), "symbol", None)}
        for v in tasks.values() if v.get("node")
    ]
    spans.sort(key=lambda s: (s["start_ms"], s["node"]))
    news = {k: [c.model_dump(mode="json") for c in v]
            for k, v in (snap.values.get("news") or {}).items()}
    return (events, list(snap.values.get("assessments", [])), spans, ckpts,
            {"thread_id": config["configurable"]["thread_id"]}, news)



def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/showcase-trace.json")
    ap.add_argument("--as-of", default="2026-05-11")
    ap.add_argument("--seeds", default="AAPL,AVGO,TSLA")
    args = ap.parse_args()

    global DB_HANDLE, EXCLUDED
    DB_HANDLE = connect()
    EXCLUDED = [ln.split(",")[0] for ln in
                pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln]
    print(f"  excluding {len(EXCLUDED)} ETFs from multi-hop paths")

    from sentence_transformers import SentenceTransformer

    from lagmatrix.adapters.vector import NewsIndex
    model = SentenceTransformer("all-MiniLM-L6-v2")

    counts, _ = query("RETURN {equity: LENGTH(equity), supplies: LENGTH(supplies_to), "
                      "comention: LENGTH(co_mentioned), articles: LENGTH(article)}", {})
    meta = counts[0]
    print(f"  corpus: {meta}")

    candidates = []
    t0 = time.perf_counter()

    seeds = [s.strip() for s in args.seeds.split(",")]
    events, assessments, spans, ckpts, run_meta, pipeline_news = asyncio.run(
        run_pipeline(seeds, args.as_of, 6))
    print(f"  pipeline: {len(events)} node updates, {len(assessments)} assessments")

    for seed in seeds:
        rows, ms = query(TRAVERSAL_AQL,
                         {"start": f"equity/{seed}", "hops": 2, "as_of": args.as_of})
        h1 = [r for r in rows if r["hops"] == 1]
        h2 = [r for r in rows if r["hops"] == 2]

        q = (f"{seed} supply chain, suppliers, component demand, "
             f"orders and production outlook")
        vec = [round(float(x), 5) for x in
               model.encode([q], normalize_embeddings=True)[0]]
        arts, vms = query(VECTOR_AQL, {"vec": vec, "k": 6, "as_of": args.as_of})

        two, tms = query(COMENTION_AQL,
                         {"start": f"equity/{seed}", "as_of": args.as_of,
                          "minpmi": 2.0, "excluded": EXCLUDED})

        for r in h1 + h2:
            sent, pct = disclosure(r.get("says") or "", r.get("named"))
            # The +/-420-char window often opens mid-sentence, so what comes back
            # can be a page header ("57 Table of Contents Qorvo, Inc. ...") or a
            # row of a financial table rather than prose. 133 of the 818 surviving
            # edges are like this. The relationship may still be real -- KHC->WMT
            # is -- but we cannot show a reader the sentence that proves it, so
            # the page marks the edge unverified instead of quoting a fragment at
            # them or silently dropping a true edge.
            quotable = bool(sent) and sent[:1].isupper()
            r["verified"] = quotable
            r["sentence"] = sent if quotable else None
            r["pct"] = pct if quotable else None
            r.pop("says", None)     # the raw window is noise on the page

        candidates.append({
            "symbol": seed,
            "as_of": args.as_of,
            "traversal_ms": ms,
            "vector_ms": vms,
            "query": q,
            "hop1": h1,
            "hop2": h2,
            "articles": arts,
            # what `vector_retriever` itself retrieved, so the page shows the
            # pipeline's own result rather than a second query run beside it
            "pipeline_news": pipeline_news.get(f"{seed}|{args.as_of}", []),
            "twohop": two, "twohop_ms": tms,
            # What this node asked before D-83. Both are real pipeline queries --
            # this one ran in production for two phases -- so showing them side by
            # side is a changelog, not a staged comparison.
            "legacy_query": LEGACY_QUERY.format(symbol=seed, direction="up"),
            "query_now": f"{seed} catalyst: earnings, demand, guidance, production, regulation",
            "legacy_news": [
                {"text": c.text, "score": round(c.score, 4),
                 "published_at": str(c.published_at)}
                for c in NewsIndex(DB_HANDLE).search(
                    LEGACY_QUERY.format(symbol=seed, direction="up"),
                    [seed], 6, date.fromisoformat(args.as_of))
            ],
        })


    trace = {
        "meta": meta | {
            "as_of": args.as_of,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "arangodb": "3.12.11",
            "embedding_model": "all-MiniLM-L6-v2 (384-dim)",
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
        "aql": {"traversal": TRAVERSAL_AQL.strip(), "vector": VECTOR_AQL.strip(),
                "comention": COMENTION_AQL.strip()},
        "events": events,
        "spans": spans,
        "checkpoints": ckpts,
        "run": run_meta,
        "candidates": candidates,
        "assessments": [
            {"symbol": a.candidate.symbol, "direction": a.candidate.direction,
             "verdict": a.verdict, "effective_evidence": round(a.effective_evidence, 3),
             "n_supporting": len(a.supporting), "n_contradicting": len(a.contradicting)}
            for a in assessments
        ],
    }
    with open(args.out, "w") as fh:
        json.dump(trace, fh, indent=1)
    print(f"  wrote {args.out}")
    for c in candidates:
        print(f"    {c['symbol']}: {len(c['hop1'])}+{len(c['hop2'])} suppliers, "
              f"{len(c['articles'])} articles, "
              f"{c['traversal_ms']}ms graph / {c['vector_ms']}ms vector")


if __name__ == "__main__":
    main()
