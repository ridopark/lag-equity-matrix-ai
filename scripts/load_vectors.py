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
import pathlib
import re
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from lagmatrix import pg  # noqa: E402

DB = "lagmatrix"
DIM = 384
CHUNK = 400


def arango():
    """The live ArangoDB handle, over HTTP (Q-54).

    This used to be `ssh <host> kubectl -n lagmatrix exec -i deploy/arangodb --
    arangosh`, which needed a kubeconfig and put the database password on a
    remote command line. `serve.arango_db()` reads the same credential file and
    speaks the HTTP API, so it works from a pod and from a laptop alike.
    """
    import serve

    db = serve.arango_db()
    if db is None:
        sys.exit("ArangoDB not reachable")
    return db


def ensure_article(db) -> None:
    """Create the `article` collection if absent -- never drop it.

    D-97: the two lines this replaces were
    `if (db._collection("article")) { db._drop("article"); } db._create("article");`
    and re-running them destroyed 47,640 embeddings and their vector index once
    already. Documents are upserted by their stable Alpaca id below
    (`overwrite_mode="replace"`), so a re-run refreshes what it re-embeds and
    leaves everything else in place -- which is what makes a nightly job safe.
    """
    if not db.has_collection("article"):
        db.create_collection("article")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-01-01")
    args = ap.parse_args()

    # Both interpolations below land in SQL executed against the *copytrade*
    # namespace's Postgres, so neither may carry an unvalidated value.
    # `--since` in particular is not always operator-typed: daily_ingest.py
    # reads it back out of ArangoDB (`MAX(a.date)`), which makes anything stored
    # there a second-order injection source. Parse it as a date, and let a bad
    # value fail loudly here rather than reach psql.
    try:
        since = date.fromisoformat(args.since).isoformat()
    except (TypeError, ValueError):
        sys.exit(f"--since must be an ISO date, got {args.since!r}")
    tickers = sorted(pd.read_csv("data/fires.csv").ticker.unique())
    if not all(re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", str(x)) for x in tickers):
        sys.exit("data/fires.csv holds a ticker that is not a plain symbol")
    # Tickers and `since` travel as parameters, not interpolated text. The
    # regex above stays as a second line of defence, but the parameterisation
    # is what makes D-103 structurally hard to repeat.
    sql = """
      WITH ok AS (SELECT article_id FROM lagmatrix.news_symbol
                  GROUP BY 1 HAVING count(*) <= 8),
      hit AS (SELECT DISTINCT s.article_id FROM lagmatrix.news_symbol s
              JOIN ok USING (article_id) WHERE s.symbol = ANY(%s))
      SELECT a.id, a.created_at::date, coalesce(a.headline,''),
             replace(coalesce(a.summary,''), chr(31), ' '),
             (SELECT string_agg(symbol, ' ') FROM lagmatrix.news_symbol z
              WHERE z.article_id = a.id)
      FROM lagmatrix.news_article a JOIN hit ON hit.article_id = a.id
      WHERE a.created_at >= %s;"""
    conn = pg.connect()
    try:
        result = pg.rows(conn, sql, (tickers, since))
    finally:
        conn.close()
    df = pd.DataFrame(
        [[("" if v is None else str(v)) for v in row] for row in result],
        columns=["id", "date", "headline", "summary", "symbols"])
    print(f"  {len(df):,} articles since {args.since} (breadth<=8, alert-universe tagged)")

    from fastembed import TextEmbedding
    model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    text = (df.headline.fillna("") + ". " + df.summary.fillna("").str.slice(0, 600)).tolist()
    print("  embedding…")
    vecs = list(model.embed(text, batch_size=128))
    print(f"  {len(vecs):,} x {vecs[0].shape[0]} embeddings")

    db = arango()
    ensure_article(db)
    articles = db.collection("article")
    for i in range(0, len(df), CHUNK):
        part = df.iloc[i:i + CHUNK]
        docs = [{"_key": str(row.id), "date": row.date, "headline": row.headline[:300],
                 "summary": row.summary[:700], "symbols": row.symbols.split(),
                 "embedding": [round(float(x), 5) for x in vecs[i + j]]}
                for j, row in enumerate(part.itertuples())]
        articles.insert_many(docs, overwrite_mode="replace")
        if (i // CHUNK) % 20 == 0:
            print(f"    {min(i+CHUNK, len(df)):,}/{len(df):,}", flush=True)

    print("  building the vector index…")
    articles.add_index({"type": "vector", "fields": ["embedding"],
                        "params": {"metric": "cosine", "dimension": DIM, "nLists": 64}})
    print(f"  indexed: {articles.count()} articles")


if __name__ == "__main__":
    main()
