"""Ingest SEC SIC/`sicDescription` onto `equity` vertices (REQ-1).

Why not `load_arango.py`'s `bulk()`: `bulk()` writes with
`overwriteMode:"replace"` (scripts/load_arango.py:77) -- a full document
replace, sending only the fields in its payload. Reusing it here would send
`{_key, symbol, sic, sic_desc}` and, on any pre-existing `equity` document
carrying other fields, silently strip them. This script's own `upsert_js`
uses `overwriteMode:"update"` (merge: adds/overwrites named fields, leaves
everything else alone) so it can never repeat that class of accident (D-97,
Q-63).

Usage:  uv run python scripts/load_sectors.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

HOST = "ridopark@192.168.10.123"
DB = "lagmatrix"
UA = {"User-Agent": "lag-equity-matrix-ai research ridopark@gmail.com"}
SEC_RATE = 8.0            # req/s; SEC asks for <= 10
WORKERS = 6               # each request spends most of its time on the wire


class Throttle:
    """Token bucket shared by the workers, so concurrency cannot outrun SEC's limit."""

    def __init__(self, rate: float):
        self.min_gap = 1.0 / rate
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            sleep = self.last + self.min_gap - now
            if sleep > 0:
                time.sleep(sleep)
                now += sleep
            self.last = now


THROTTLE = Throttle(SEC_RATE)


def http(url: str) -> bytes:
    for attempt in range(4):
        try:
            THROTTLE.wait()
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=40).read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return b""


def arango_js(js: str, database: str = DB) -> str:
    """Run JS in arangosh (mirrors `load_arango.py`'s `arango_js`)."""
    pw = open(os.path.expanduser("~/.lagmatrix-arango-pw")).read().strip()
    remote = ("kubectl -n lagmatrix exec -i deploy/arangodb -- sh -c "
              f"'cat > /tmp/j.js && arangosh --server.password \"{pw}\" "
              f"--server.database {database} --javascript.execute /tmp/j.js'")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, remote],
                       input=js, capture_output=True, text=True, timeout=1800)
    if r.returncode:
        sys.exit(f"arangosh failed:\n{r.stderr[:1500]}\n{r.stdout[-1500:]}")
    return r.stdout


def sector_update_docs(rows) -> list[dict]:
    """Shape `(symbol, sic, sic_desc)` rows into `equity` merge-write docs.

    Rows with `sic is None` (SEC had no match) are excluded entirely, rather
    than written as `sic: null` -- that would overwrite a symbol's real SIC
    with null on a future re-run before it resolves.
    """
    return [
        {"_key": sym, "symbol": sym, "sic": sic, "sic_desc": sic_desc}
        for sym, sic, sic_desc in rows if sic is not None
    ]


def upsert_js(docs: list[dict]) -> str:
    """JS that merges `docs` onto `equity`, `overwriteMode:"update"` --
    never `_drop`, never `overwriteMode:"replace"` (D-97, Q-63)."""
    return f'db.equity.insert({json.dumps(docs)}, {{overwriteMode:"update"}});\n'


def upsert_sectors(docs: list[dict], chunk: int = 2000) -> None:
    for i in range(0, len(docs), chunk):
        arango_js(upsert_js(docs[i:i + chunk]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max symbols to fetch (0 = no limit)")
    args = ap.parse_args()

    symbols = sorted(pd.read_parquet("data/bars-10y.parquet", columns=["symbol"]).symbol.unique())
    if args.limit:
        symbols = symbols[:args.limit]

    cmap = json.loads(http("https://www.sec.gov/files/company_tickers.json"))
    by_tic = {v["ticker"]: v for v in cmap.values()}
    universe = [s for s in symbols if s in by_tic]
    print(f"  {len(symbols)} symbols, {len(universe)} matched in SEC's ticker map")

    def fetch(sym):
        cik = str(by_tic[sym]["cik_str"]).zfill(10)
        try:
            sub = json.loads(http(f"https://data.sec.gov/submissions/CIK{cik}.json"))
        except Exception:
            return sym, None, None
        return sym, sub.get("sic"), sub.get("sicDescription")

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for sym, sic, sic_desc in pool.map(fetch, universe):
            rows.append((sym, sic, sic_desc))

    docs = sector_update_docs(rows)
    resolved, total = len(docs), len(symbols)
    print(f"  {resolved}/{total} symbols resolved ({resolved / total:.1%} coverage)")

    upsert_sectors(docs)
    print(f"  upserted sic/sic_desc onto {len(docs)} equity documents")


if __name__ == "__main__":
    main()
