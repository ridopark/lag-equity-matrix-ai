"""Backfill Alpaca/Benzinga news into lagmatrix.news_* (D-16's unbuilt half).

Bulk-loads via COPY through `ssh -> kubectl exec -i -> psql`, because that is
the only route to this cluster: 5432 is a headless ClusterIP with no external
route, so row-at-a-time INSERT would cost one round trip each and is unusable.

Idempotent. Articles arrive with a stable Alpaca id, staged into a TEMP table
and merged with ON CONFLICT DO NOTHING, so re-running a window is free and a
half-finished backfill can simply be run again.

Coverage is recorded per (symbol, window) so a resumed run skips what it has,
and so an empty window is distinguishable from one never fetched.

Usage:
  uv run python scripts/load_news.py --symbols AAPL,NVDA --start 2016-01-01
  uv run python scripts/load_news.py --from-fires --years 10
  uv run python scripts/load_news.py --top-liquid 100 --years 10
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

HOST = "ridopark@192.168.10.123"
PSQL = ("kubectl -n copytrade exec -i postgres-0 -- "
        "psql -U temporal -d orchestrator -v ON_ERROR_STOP=1 -q")
CHUNK_DAYS = 90       # one fetch window; Benzinga paginates within it
PAGE_LIMIT = 50       # Alpaca's per-request cap


def psql(sql: str, stdin: str = "") -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, PSQL],
                       input=sql + stdin, capture_output=True, text=True, timeout=600)
    if r.returncode:
        sys.exit(f"psql failed:\n{r.stderr[:1500]}")
    return r.stdout


def already_covered(symbol: str) -> set[tuple[date, date]]:
    out = psql("\\pset tuples_only on\n"
               f"SELECT window_start||'|'||window_end FROM lagmatrix.news_coverage "
               f"WHERE symbol = '{symbol}';\n")
    got = set()
    for line in out.splitlines():
        line = line.strip()
        if "|" in line:
            a, b = line.split("|")
            got.add((date.fromisoformat(a), date.fromisoformat(b)))
    return got


def copy_in(table: str, columns: list[str], rows: list[list]) -> None:
    """One COPY per batch. Round trips are the expensive thing here, not bytes."""
    if not rows:
        return
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    w.writerows(rows)
    cols = ", ".join(columns)
    sql = (
        # the CREATE must be inside the transaction: outside it, ON COMMIT DROP
        # fires at the end of its own implicit transaction and the table is gone
        "BEGIN;\n"
        f"CREATE TEMP TABLE stage (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP;\n"
        f"COPY stage ({cols}) FROM STDIN WITH (FORMAT csv);\n"
        f"{buf.getvalue()}\\.\n"
        f"INSERT INTO {table} ({cols}) SELECT {cols} FROM stage "
        f"ON CONFLICT DO NOTHING;\n"
        "COMMIT;\n"
    )
    psql(sql)


def fetch_window(client: NewsClient, symbol: str, start: datetime, end: datetime):
    """All articles for one symbol/window, following Alpaca's page token."""
    arts, token = [], None
    while True:
        req = NewsRequest(symbols=symbol, start=start, end=end,
                          limit=PAGE_LIMIT, page_token=token,
                          include_content=False, sort="asc")
        resp = client.get_news(req)
        page = resp.data.get("news", []) if hasattr(resp, "data") else []
        arts.extend(page)
        token = getattr(resp, "next_page_token", None)
        if not token or not page:
            return arts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols")
    ap.add_argument("--from-fires", action="store_true",
                    help="use the tickers the signal feed has alerted on")
    ap.add_argument("--top-liquid", type=int, default=0)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--start")
    args = ap.parse_args()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.from_fires:
        symbols = sorted(pd.read_csv("data/fires.csv").ticker.unique())
    elif args.top_liquid:
        recent = pd.read_parquet("data/bars.parquet")
        funds = {r["symbol"] for r in csv.DictReader(open("data/excluded-etfs.csv"))}
        dv = recent.groupby("symbol").dollar_vol.median().sort_values(ascending=False)
        symbols = [s for s in dv.index if s not in funds][: args.top_liquid]
    else:
        sys.exit("give --symbols, --from-fires or --top-liquid")

    end = datetime.now(UTC)
    start = (datetime.fromisoformat(args.start).replace(tzinfo=UTC) if args.start
             else end - timedelta(days=365 * args.years))
    print(f"  {len(symbols)} symbols, {start.date()} -> {end.date()}")

    client = NewsClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    tot_new = tot_seen = 0

    for i, sym in enumerate(symbols, 1):
        covered = already_covered(sym)
        w_start, sym_new, sym_seen, skipped = start, 0, 0, 0
        while w_start < end:
            w_end = min(w_start + timedelta(days=CHUNK_DAYS), end)
            key = (w_start.date(), w_end.date())
            if key in covered:
                skipped += 1
                w_start = w_end
                continue
            arts = fetch_window(client, sym, w_start, w_end)
            if arts:
                copy_in("lagmatrix.news_article",
                        ["id", "created_at", "updated_at", "headline", "author",
                         "source", "summary", "url"],
                        [[a.id, a.created_at.isoformat(),
                          a.updated_at.isoformat() if a.updated_at else "",
                          a.headline or "", a.author or "", a.source or "",
                          (a.summary or "")[:20000], a.url or ""] for a in arts])
                copy_in("lagmatrix.news_symbol", ["article_id", "symbol"],
                        [[a.id, s] for a in arts for s in (a.symbols or [])])
                sym_new += len(arts)
            sym_seen += len(arts)
            psql("INSERT INTO lagmatrix.news_coverage "
                 "(symbol, window_start, window_end, n_articles) VALUES "
                 f"('{sym}', '{key[0]}', '{key[1]}', {len(arts)}) "
                 "ON CONFLICT (symbol, window_start, window_end) DO UPDATE "
                 "SET n_articles = EXCLUDED.n_articles, fetched_at = now();\n")
            w_start = w_end
        tot_new += sym_new
        tot_seen += sym_seen
        print(f"    {i}/{len(symbols)} {sym:<6} {sym_seen:>6} articles"
              f"{f'  ({skipped} windows already covered)' if skipped else ''}")

    print(f"\n  fetched {tot_seen:,} article-rows across {len(symbols)} symbols")
    n = psql("\\pset tuples_only on\n"
             "SELECT (SELECT count(*) FROM lagmatrix.news_article)||' articles, '||"
             "(SELECT count(*) FROM lagmatrix.news_symbol)||' symbol tags, '||"
             "(SELECT count(DISTINCT symbol) FROM lagmatrix.news_symbol)||' symbols';\n")
    print(f"  table now holds: {n.strip()}")


if __name__ == "__main__":
    main()
