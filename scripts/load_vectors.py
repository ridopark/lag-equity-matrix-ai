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
from lagmatrix.ingest import plan_embeddings  # noqa: E402

DB = "lagmatrix"
DIM = 384
CHUNK = 400
# The candidate query's lower bound (Q-56). Fixed, not the moving watermark
# `daily_ingest.py` passes as `--since`: a ticker firing for the first time
# after that watermark had already advanced past its own history was never
# picked up as a candidate at all, so the anti-join below (against what is
# already embedded) never got a chance to catch it -- 412 articles measured
# missing this way. `--since` keeps its default and its validation, but no
# longer bounds this query; see `main()`.
CORPUS_FLOOR = "2025-01-01"


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
    ap.add_argument(
        "--since", default="2025-01-01",
        help="Informational only -- it does NOT bound the query. Candidates come "
             "from CORPUS_FLOOR and the work is whatever is not yet embedded (Q-56). "
             "Kept because daily_ingest.py passes it and because a malformed value "
             "is still an operator mistake worth catching. A flag that looks like it "
             "narrows the query and silently does not would be the same class of "
             "defect this fix removes.")
    args = ap.parse_args()

    # Both interpolations below land in SQL executed against the *copytrade*
    # namespace's Postgres, so neither may carry an unvalidated value.
    # `--since` in particular is not always operator-typed: daily_ingest.py
    # reads it back out of ArangoDB (`MAX(a.date)`), which makes anything stored
    # there a second-order injection source. Parse it as a date, and let a bad
    # value fail loudly here rather than reach psql -- it is a policy floor
    # now (see `CORPUS_FLOOR` above), not the query's bound, but a malformed
    # `--since` is still an operator mistake worth catching.
    try:
        since = date.fromisoformat(args.since).isoformat()
    except (TypeError, ValueError):
        sys.exit(f"--since must be an ISO date, got {args.since!r}")
    tickers = sorted(pd.read_csv("data/fires.csv").ticker.unique())
    if not all(re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", str(x)) for x in tickers):
        sys.exit("data/fires.csv holds a ticker that is not a plain symbol")
    # Tickers and the corpus floor travel as parameters, not interpolated
    # text. The regex above stays as a second line of defence, but the
    # parameterisation is what makes D-103 structurally hard to repeat.
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
        result = pg.rows(conn, sql, (tickers, CORPUS_FLOOR))
    finally:
        conn.close()
    df = pd.DataFrame(
        [[("" if v is None else str(v)) for v in row] for row in result],
        columns=["id", "date", "headline", "summary", "symbols"])

    db = arango()
    ensure_article(db)
    articles = db.collection("article")
    existing_keys = set(db.aql.execute("FOR a IN article RETURN a._key"))
    n_candidates, n_already_embedded, to_embed = plan_embeddings(df.id.tolist(), existing_keys)
    print(f"  {n_candidates:,} candidates since {CORPUS_FLOOR} "
          f"(breadth<=8, alert-universe tagged; --since {since} is a policy floor only)")
    print(f"  {n_already_embedded:,} already embedded")
    print(f"  {len(to_embed):,} to embed")
    df = df[df.id.isin(to_embed)].reset_index(drop=True)

    from fastembed import TextEmbedding
    model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    text = (df.headline.fillna("") + ". " + df.summary.fillna("").str.slice(0, 600)).tolist()
    print("  embedding…")
    vecs = list(model.embed(text, batch_size=128)) if text else []
    if vecs:
        print(f"  {len(vecs):,} x {vecs[0].shape[0]} embeddings")

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
