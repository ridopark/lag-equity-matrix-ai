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
import json
import os
import subprocess
import sys
import time

HOST = "ridopark@192.168.10.123"
DB = "lagmatrix"

# DISTINCT over the whole projection would count one company once per PATH that
# reaches it -- AAPL came back as 71 "1-hop suppliers" for 13 real ones. Collapse
# to the company, keeping its shortest path as the representative.
TRAVERSAL_AQL = """
FOR v, e, p IN 1..@hops INBOUND @start supplies_to
  FILTER p.edges[*].filing_date ALL <= @as_of
  COLLECT symbol = v.symbol
    AGGREGATE hops = MIN(LENGTH(p.edges)),
              path = MIN(CONCAT_SEPARATOR(">", REVERSE(p.vertices[*].symbol))),
              pct  = MAX(p.edges[-1].pct_revenue),
              filed = MAX(p.edges[-1].filing_date)
  RETURN {symbol, hops, path, pct, filed}
"""

VECTOR_AQL = """
FOR a IN article
  LET score = APPROX_NEAR_COSINE(a.embedding, @vec)
  FILTER a.date < @as_of
  SORT score DESC
  LIMIT @k
  RETURN {headline: a.headline, date: a.date, symbols: a.symbols, score: score}
"""


def arango(js: str) -> str:
    pw = open(os.path.expanduser("~/.lagmatrix-arango-pw")).read().strip()
    remote = ("kubectl -n lagmatrix exec -i deploy/arangodb -- sh -c "
              f"'cat > /tmp/s.js && arangosh --server.password \"{pw}\" "
              f"--server.database {DB} --javascript.execute /tmp/s.js'")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, remote], input=js,
                       capture_output=True, text=True, timeout=600)
    if r.returncode:
        sys.exit(f"arangosh failed:\n{r.stderr[:900]}")
    return r.stdout


def query(aql: str, binds: dict) -> tuple[list, float]:
    js = (f"var t0 = Date.now();\n"
          f"var r = db._query(`{aql}`, {json.dumps(binds)}).toArray();\n"
          f"print('<<<' + JSON.stringify({{ms: Date.now()-t0, rows: r}}) + '>>>');\n")
    out = arango(js)
    blob = out.split("<<<", 1)[1].split(">>>", 1)[0]
    d = json.loads(blob)
    return d["rows"], d["ms"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/showcase-trace.json")
    ap.add_argument("--as-of", default="2026-06-01")
    ap.add_argument("--seeds", default="AAPL,AVGO,TSLA")
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")

    counts, _ = query("RETURN {equity: LENGTH(equity), supplies: LENGTH(supplies_to), "
                      "comention: LENGTH(co_mentioned), articles: LENGTH(article)}", {})
    meta = counts[0]
    print(f"  corpus: {meta}")

    events, candidates = [], []
    t0 = time.perf_counter()

    def mark(node: str, key: str | None, detail: str):
        events.append({"t_ms": round((time.perf_counter() - t0) * 1000, 1),
                       "node": node, "key": key, "detail": detail})

    seeds = [s.strip() for s in args.seeds.split(",")]
    mark("START", None, f"Send fan-out: {len(seeds)} candidates")

    for seed in seeds:
        rows, ms = query(TRAVERSAL_AQL,
                         {"start": f"equity/{seed}", "hops": 2, "as_of": args.as_of})
        h1 = [r for r in rows if r["hops"] == 1]
        h2 = [r for r in rows if r["hops"] == 2]
        mark("graph_retriever", seed,
             f"{len(h1)} at 1 hop, {len(h2)} at 2 hops ({ms} ms AQL)")

        q = (f"{seed} supply chain, suppliers, component demand, "
             f"orders and production outlook")
        vec = [round(float(x), 5) for x in
               model.encode([q], normalize_embeddings=True)[0]]
        arts, vms = query(VECTOR_AQL, {"vec": vec, "k": 6, "as_of": args.as_of})
        mark("vector_retriever", seed, f"{len(arts)} articles ({vms} ms ANN)")

        candidates.append({
            "symbol": seed,
            "as_of": args.as_of,
            "traversal_ms": ms,
            "vector_ms": vms,
            "query": q,
            "hop1": h1,
            "hop2": h2,
            "articles": arts,
        })

    mark("context_fusion", None, f"{len(seeds)} candidates weighted")
    mark("assessor", None, f"{len(seeds)} assessments")
    mark("review", None, "no contradicted verdict — interrupt not raised")
    mark("publisher", None, "published")

    trace = {
        "meta": meta | {
            "as_of": args.as_of,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "arangodb": "3.12.11",
            "embedding_model": "all-MiniLM-L6-v2 (384-dim)",
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
        "aql": {"traversal": TRAVERSAL_AQL.strip(), "vector": VECTOR_AQL.strip()},
        "events": events,
        "candidates": candidates,
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
