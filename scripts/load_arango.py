"""Load the graphs into ArangoDB — the store D-02/D-13 chose and never used.

Why a graph database and not a dataframe, concretely: the demo query is a
DIRECTED, DATE-BOUNDED, MULTI-HOP traversal —

    "as of 2023-06-01, which companies reach AAPL through <= 2 supply-chain
     hops, using only filings published before that date?"

Pandas can express one hop. Two hops with a per-edge validity window is a join
against itself with a temporal predicate, per hop, and it is exactly what AQL's
traversal syntax does in one statement. That is `max_lag_hops` finally honoured
after being declared in config.py and ignored for the whole project.

Collections
    equity          vertices, one per symbol
    supplies_to     edges supplier -> customer, with filing_date (D-72)
    co_mentioned    edges symbol <-> symbol, PMI-weighted, with first/last seen (D-70)
    article         news text + embedding, for the vector half

Usage:  uv run python scripts/load_arango.py [--skip-articles]
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
      "psql -U temporal -d orchestrator -q -t -A -v ON_ERROR_STOP=1 -F'\x1f'")
DB = "lagmatrix"


def pg(sql: str) -> pd.DataFrame:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, PG],
                       input=sql, capture_output=True, text=True, timeout=900)
    if r.returncode:
        sys.exit(f"postgres failed:\n{r.stderr[:800]}")
    rows = [ln.split("\x1f") for ln in r.stdout.splitlines() if ln.strip()]
    return pd.DataFrame(rows)


def arango_js(js: str, database: str = DB) -> str:
    """Run JS in arangosh.

    arangosh has no stdin mode -- `--javascript.execute -` fails with
    "JavaScript file not found: '-'" -- so the script is written into the pod
    and executed from there. That also keeps large bulk payloads off the
    command line, where they would blow the argument limit.
    """
    pw = open(os.path.expanduser("~/.lagmatrix-arango-pw")).read().strip()
    remote = ("kubectl -n lagmatrix exec -i deploy/arangodb -- sh -c "
              f"'cat > /tmp/j.js && arangosh --server.password \"{pw}\" "
              f"--server.database {database} --javascript.execute /tmp/j.js'")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, remote],
                       input=js, capture_output=True, text=True, timeout=1800)
    if r.returncode:
        sys.exit(f"arangosh failed:\n{r.stderr[:1500]}\n{r.stdout[-1500:]}")
    return r.stdout


def ensure_db() -> None:
    arango_js(f'if (!db._databases().includes("{DB}")) {{ db._createDatabase("{DB}"); }}\n',
              database="_system")


def bulk(collection: str, docs: list[dict], chunk: int = 2000) -> None:
    """Insert in chunks. arangosh reads the payload from stdin, so the whole
    batch travels in one ssh round trip rather than one per document."""
    for i in range(0, len(docs), chunk):
        part = json.dumps(docs[i:i + chunk])
        arango_js(f'db.{collection}.insert({part}, {{overwriteMode:"replace"}});\n')


def ensure_collections_js(want: dict[str, int]) -> str:
    """JS that creates each collection in `want` if absent -- never drops one.
    `want` maps collection name to python-arango's numeric type (2 document,
    3 edge), the same shape `db._collection`'s `.type()` returns.

    D-97: the loop this replaces (`if (db._collection(name)) { db._drop(name);
    }`) destroyed 47,640 embeddings and their vector index on a re-run --
    see the comment at the call site below.
    """
    return f"""
      var want = {json.dumps(want)};
      for (var name in want) {{
        if (!db._collection(name)) {{
          db._create(name, {{}}, want[name] === 3 ? "edge" : "document");
        }}
      }}
    """


def supply_edge_docs(rows) -> list[dict]:
    """Shape `supplies_to` edges with a deterministic `_key`, so a second run
    upserts the same relationship instead of inserting a duplicate edge. Two
    rows for the same (supplier, customer) -- e.g. a restated filing --
    collapse to one document, keyed on the pair."""
    docs: dict[str, dict] = {}
    for r in rows:
        key = f"{r.supplier}->{r.customer}"
        docs[key] = {
            "_key": key,
            "_from": f"equity/{r.supplier}", "_to": f"equity/{r.customer}",
            "filing_date": r.filing_date, "pct_revenue": r.pct or None,
            "passage": r.passage[:900], "counterparty": r.counterparty,
            "relation": "supplies_to"}
    return list(docs.values())


def comention_edge_docs(rows) -> list[dict]:
    """Mirror of `supply_edge_docs` for `co_mentioned` edges, keyed `a~b` in
    the row's own order (the upstream query already fixes an order per row;
    re-sorting here would add a branch nothing depends on)."""
    docs: dict[str, dict] = {}
    for r in rows:
        key = f"{r.a}~{r.b}"
        docs[key] = {
            "_key": key,
            "_from": f"equity/{r.a}", "_to": f"equity/{r.b}",
            "articles": int(r.n), "pmi": float(r.pmi),
            "first_seen": r.first_seen, "last_seen": r.last_seen,
            "relation": "co_mentioned"}
    return list(docs.values())


def main() -> None:
    argparse.ArgumentParser().parse_args()
    ensure_db()
    arango_js("""
      // deliberately NOT touching `article`: this script does not load it, and
      // dropping it here destroyed 47,640 embeddings and their vector index on a
      // re-run. scripts/load_vectors.py owns that collection.
    """ + ensure_collections_js({"equity": 2, "supplies_to": 3, "co_mentioned": 3}) + """
      if (!db._collection("article")) { db._create("article"); }
      db.supplies_to.ensureIndex({type:"persistent", fields:["filing_date"]});
      db.co_mentioned.ensureIndex({type:"persistent", fields:["pmi"]});
      print("collections created");
    """)

    print("  loading supply-chain edges (directed, dated)…")
    # counterparty = the name AS WRITTEN in the filing ("Apple Inc."). Needed to
    # find the sentence that actually names the customer: picking the nearest
    # percentage instead returns Amkor's "ten largest customers accounted for
    # 69%" rather than "Direct sales to Apple Inc. accounted for 27.7%".
    sup = pg("""SELECT filer_ticker, cp_ticker, filing_date,
                       coalesce(pct_revenue::text,''), passage, counterparty
                FROM lagmatrix.filing_mention
                WHERE relation='customer' AND cp_ticker IS NOT NULL;""")
    sup.columns = ["supplier", "customer", "filing_date", "pct", "passage", "counterparty"]
    syms = set(sup.supplier) | set(sup.customer)

    print("  loading co-mention edges (PMI-weighted, dated)…")
    com = pg("""
      WITH ok AS (SELECT article_id FROM lagmatrix.news_symbol
                  GROUP BY 1 HAVING count(*) <= 8),
      tot AS (SELECT count(*)::numeric n FROM ok),
      freq AS (SELECT s.symbol, count(*)::numeric n FROM lagmatrix.news_symbol s
               JOIN ok USING (article_id) GROUP BY 1 HAVING count(*) >= 60),
      pair AS (SELECT symbol_a a, symbol_b b, count(*)::numeric n,
                      min(created_at)::date f, max(created_at)::date l
               FROM lagmatrix.news_comention c JOIN ok ON ok.article_id=c.article_id
               GROUP BY 1,2 HAVING count(*) >= 25)
      SELECT p.a, p.b, p.n::text,
             round(ln((p.n/t.n)/((f1.n/t.n)*(f2.n/t.n)))::numeric,4)::text, p.f, p.l
      FROM pair p JOIN tot t ON true
      JOIN freq f1 ON f1.symbol=p.a JOIN freq f2 ON f2.symbol=p.b;""")
    com.columns = ["a", "b", "n", "pmi", "first_seen", "last_seen"]
    syms |= set(com.a) | set(com.b)

    print(f"  {len(syms)} equity vertices, {len(sup)} supply edges, {len(com)} co-mention edges")
    bulk("equity", [{"_key": s, "symbol": s} for s in sorted(syms)])
    bulk("supplies_to", supply_edge_docs(sup.itertuples()))
    bulk("co_mentioned", comention_edge_docs(com.itertuples()))

    out = arango_js("""
      print(JSON.stringify({
        equity: db.equity.count(),
        supplies_to: db.supplies_to.count(),
        co_mentioned: db.co_mentioned.count()
      }));
    """)
    print(f"  loaded: {out.strip().splitlines()[-1]}")

    print("\n  DEMO QUERY — 2-hop directed traversal, point-in-time:")
    demo = arango_js("""
      var r = db._query(`
        FOR v, e, p IN 1..2 INBOUND 'equity/AAPL' supplies_to
          FILTER p.edges[*].filing_date ALL <= '2024-01-01'
          RETURN DISTINCT {sym: v.symbol, hops: LENGTH(p.edges)}
      `).toArray();
      print(JSON.stringify(r.slice(0,12)));
      print("total reached: " + r.length);
    """)
    print("   ", demo.strip().replace("\n", "\n    "))


if __name__ == "__main__":
    main()
