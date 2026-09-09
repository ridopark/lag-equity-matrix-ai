"""Embed the news corpus and index it in ArangoDB — the vector half of D-13.

`vector_retriever` currently calls the Alpaca News API live. That is not
semantic retrieval, it is a keyword-ish symbol lookup over a vendor endpoint,
and it made `adapters/vector.py` a 14-line stub for the whole project.

This gives the pipeline a real corpus with a real ANN index:
  - articles tagging <= 8 symbols (D-70's hub filter), 2025-01-01 onward,
    mentioning at least one alert-universe ticker
  - all-MiniLM-L6-v2, 384-dim, on CPU (~314 docs/sec measured)
  - ArangoDB vector index, cosine, queried with APPROX_NEAR_COSINE

Recency-bounded rather than symbol-bounded on purpose: symbol filtering alone
does not bound volume (208k articles at breadth<=8), recency does.

Usage:  uv run python scripts/load_vectors.py [--since 2025-01-01]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import pandas as pd

HOST = "ridopark@192.168.10.123"
PG = ("kubectl -n copytrade exec -i postgres-0 -- "
      "psql -U temporal -d orchestrator -q -t -A -F'\x1f'")
DB = "lagmatrix"
DIM = 384
CHUNK = 400


def arango_js(js: str, database: str = DB) -> str:
    pw = open(os.path.expanduser("~/.lagmatrix-arango-pw")).read().strip()
    remote = ("kubectl -n lagmatrix exec -i deploy/arangodb -- sh -c "
              f"'cat > /tmp/v.js && arangosh --server.password \"{pw}\" "
              f"--server.database {database} --javascript.execute /tmp/v.js'")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, remote],
                       input=js, capture_output=True, text=True, timeout=1800)
    if r.returncode:
        sys.exit(f"arangosh failed:\n{r.stderr[:1200]}\n{r.stdout[-1200:]}")
    return r.stdout


def ensure_article_js() -> str:
    """JS that creates the `article` collection if absent -- never drops it.

    D-97: the two lines this replaces were
    `if (db._collection("article")) { db._drop("article"); } db._create("article");`
    and re-running them destroyed 47,640 embeddings and their vector index once
    already. Documents are upserted by their stable Alpaca id below
    (`overwriteMode:'replace'`), so a re-run refreshes what it re-embeds and
    leaves everything else in place -- which is what makes a nightly job safe.
    """
    return """
      if (!db._collection("article")) { db._create("article"); }
      print("article collection ready");
    """


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-01-01")
    args = ap.parse_args()

    tickers = sorted(pd.read_csv("data/fires.csv").ticker.unique())
    inlist = ",".join(f"'{t}'" for t in tickers)
    sql = f"""
      WITH ok AS (SELECT article_id FROM lagmatrix.news_symbol
                  GROUP BY 1 HAVING count(*) <= 8),
      hit AS (SELECT DISTINCT s.article_id FROM lagmatrix.news_symbol s
              JOIN ok USING (article_id) WHERE s.symbol IN ({inlist}))
      SELECT a.id, a.created_at::date, coalesce(a.headline,''),
             replace(coalesce(a.summary,''), chr(31), ' '),
             (SELECT string_agg(symbol, ' ') FROM lagmatrix.news_symbol z
              WHERE z.article_id = a.id)
      FROM lagmatrix.news_article a JOIN hit ON hit.article_id = a.id
      WHERE a.created_at >= '{args.since}';"""
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, PG], input=sql,
                       capture_output=True, text=True, timeout=1200)
    if r.returncode:
        sys.exit(f"postgres failed:\n{r.stderr[:600]}")
    rows = [ln.split("\x1f") for ln in r.stdout.splitlines() if ln.count("\x1f") == 4]
    df = pd.DataFrame(rows, columns=["id", "date", "headline", "summary", "symbols"])
    print(f"  {len(df):,} articles since {args.since} (breadth<=8, alert-universe tagged)")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    text = (df.headline.fillna("") + ". " + df.summary.fillna("").str.slice(0, 600)).tolist()
    print("  embedding…")
    vecs = model.encode(text, batch_size=128, show_progress_bar=False,
                        normalize_embeddings=True)
    print(f"  {vecs.shape[0]:,} x {vecs.shape[1]} embeddings")

    arango_js(ensure_article_js())
    for i in range(0, len(df), CHUNK):
        part = df.iloc[i:i + CHUNK]
        docs = [{"_key": str(row.id), "date": row.date, "headline": row.headline[:300],
                 "summary": row.summary[:700], "symbols": row.symbols.split(),
                 "embedding": [round(float(x), 5) for x in vecs[i + j]]}
                for j, row in enumerate(part.itertuples())]
        arango_js(f"db.article.insert({json.dumps(docs)}, {{overwriteMode:'replace'}});\n")
        if (i // CHUNK) % 20 == 0:
            print(f"    {min(i+CHUNK, len(df)):,}/{len(df):,}", flush=True)

    print("  building the vector index…")
    out = arango_js(f"""
      db.article.ensureIndex({{type:"vector", fields:["embedding"],
        params:{{metric:"cosine", dimension:{DIM}, nLists:64}}}});
      print("indexed: " + db.article.count() + " articles");
    """)
    print("  " + out.strip().splitlines()[-1])


if __name__ == "__main__":
    main()
