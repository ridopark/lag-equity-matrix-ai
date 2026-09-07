"""Extract directed company-to-company edges from 10-K text into Postgres.

Why this edge and not the two we already have. Correlation is symmetric, so it
cannot express a lead-lag hypothesis (D-59/D-60 measured lag 0). Co-mention is
undirected. A 10-K disclosure is neither: the filer names a counterparty in its
own document, and QRVO names Apple while Apple never names QRVO.

What the data actually supports, measured before building this:
  - Large filers ANONYMISE. NVDA: "one direct customer represented 22% of total
    revenue". AVGO and MU likewise. ASC 280 compels the magnitude, not the name.
  - Suppliers NAME. 14 of 20 sampled suppliers name a large customer. The edge
    is therefore built from the small end pointing up, which is also the
    direction Cohen & Frazzini found predictive.

Design. `passage` is the durable artefact; `relation` is a fallible reading of
it. The heuristic labeller here is deliberately conservative and marked
`confidence='heuristic'`, so an LLM pass can re-label from stored text without
re-crawling EDGAR, and every edge stays auditable against its filing.

Usage:
  uv run python scripts/load_edgar.py --top-liquid 300 --filings 3
  uv run python scripts/load_edgar.py --tickers QRVO,CRUS,SWKS
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
import time
import urllib.request

import pandas as pd

HOST = "ridopark@192.168.10.123"
PSQL = ("kubectl -n copytrade exec -i postgres-0 -- "
        "psql -U temporal -d orchestrator -v ON_ERROR_STOP=1 -q")
UA = {"User-Agent": "lag-equity-matrix-ai research ridopark@gmail.com"}
SEC_PAUSE = 0.12          # SEC asks for <= 10 req/s
WINDOW = 420              # chars either side of a counterparty mention

# Customer language must be near the name for the mention to count at all.
CUSTOMER_CTX = re.compile(
    r"customer|net revenue|total revenue|net sales|accounted for|represented", re.I)
# Strong positives: the filer is describing someone buying from it.
CUSTOMER_POS = re.compile(
    r"\b(our (?:largest |key |significant |principal |end )?customer|"
    r"end customer|customers include|sales to|revenue from|"
    r"accounted for [^.]{0,40}(?:of (?:our )?(?:net |total )?(?:revenue|sales))|"
    r"represented [^.]{0,40}(?:of (?:our )?(?:net |total )?(?:revenue|sales)))", re.I)
# Strong negatives: competitor or supplier language wins over the above.
COMPETITOR = re.compile(r"\b(compet\w+|rival|alternative to|substitutes?)\b", re.I)
SUPPLIER = re.compile(r"\b(supplier|foundry|we purchase|we source|vendor|"
                      r"manufactured (?:for|by) us|contract manufacturer)\b", re.I)
PCT = re.compile(r"(\d{1,3}(?:\.\d)?)\s?%")
# A bare short-name match is not enough. Several tickers have short names that
# are ordinary English words -- MicroStrategy renamed itself "Strategy", and
# Celsius Holdings collides with "degrees Celsius", which a semiconductor 10-K
# is full of. Requiring a corporate designator right after the name removes
# that class of false positive without a hand-maintained blocklist.
DESIGNATOR = re.compile(
    r"^[\s,.]{0,3}(Inc|Corp|Corporation|Company|Co\b|Ltd|Limited|plc|PLC|LLC|"
    r"Holdings|Technologies|Technology|Systems|Semiconductor|Group|N\.V|S\.A|AG|SE)\b")


def http(url: str) -> bytes:
    for attempt in range(4):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=40).read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return b""


def psql(sql: str) -> str:
    for attempt in range(3):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, PSQL],
                           input=sql, capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            return r.stdout
        if "ERROR:" in r.stderr:
            sys.exit(f"psql failed:\n{r.stderr[:1200]}")
        time.sleep(3 * (attempt + 1))
    sys.exit("psql transport failed")


VETO_RADIUS = 120


def label(window: str, name_at: int) -> str:
    """Customer unless a competitor/supplier cue sits CLOSE to the name.

    The veto has to be proximity-bounded. A 10-K says "competitive" on nearly
    every page, so scanning the whole +/-420 window rejects almost every real
    customer mention -- it silently zeroed out SWKS, whose filing plainly says
    "Our key customers include Amazon, Apple Inc.".
    """
    near = window[max(0, name_at - VETO_RADIUS): name_at + VETO_RADIUS]
    if COMPETITOR.search(near) or SUPPLIER.search(near):
        return "unknown"
    return "customer" if CUSTOMER_POS.search(window) else "unknown"


def clean(html: bytes) -> str:
    t = re.sub(r"<[^>]+>", " ", html.decode("utf-8", "ignore"))
    t = re.sub(r"&#\d+;|&[a-z]+;", " ", t)
    return re.sub(r"\s+", " ", t)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers")
    ap.add_argument("--top-liquid", type=int, default=0)
    ap.add_argument("--filings", type=int, default=1, help="most recent N 10-Ks each")
    args = ap.parse_args()

    cmap = json.loads(http("https://www.sec.gov/files/company_tickers.json"))
    by_tic = {v["ticker"]: v for v in cmap.values()}
    # counterparty vocabulary: every ticker's short name, longest-first so
    # "Advanced Micro Devices" wins over "Advanced"
    name_to_tic: dict[str, str] = {}
    for v in cmap.values():
        short = re.split(r"[,/]| Inc| Corp| Co\b| Ltd| plc| Holdings| Group",
                         v["title"], maxsplit=1)[0].strip()
        if len(short) >= 4:
            name_to_tic.setdefault(short, v["ticker"])
    # Counterparties are restricted to the tradeable universe. Two reasons: an
    # edge to a company we cannot trade is useless, and it keeps the alternation
    # to ~2k rather than ~10k names.
    # NOTE: sort longest-first for alternation precedence, but do NOT truncate.
    # Truncating a length-sorted list drops the SHORT names -- which is every
    # mega-cap: "Apple" is 5 characters and fell off a [:6000] slice, so no
    # filing could ever match it.
    tradeable = set(pd.read_parquet("data/bars.parquet").symbol.unique())
    vocab = sorted((n for n, tk in name_to_tic.items() if tk in tradeable),
                   key=len, reverse=True)
    NAME_RE = re.compile(r"\b(" + "|".join(re.escape(n) for n in vocab) + r")\b")
    print(f"  counterparty vocabulary: {len(vocab)} names in the tradeable universe")

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        recent = pd.read_parquet("data/bars.parquet")
        funds = {r["symbol"] for r in csv.DictReader(open("data/excluded-etfs.csv"))}
        dv = recent.groupby("symbol").dollar_vol.median().sort_values(ascending=False)
        tickers = [s for s in dv.index if s not in funds][: args.top_liquid]

    done = {ln.strip() for ln in psql("\\pset tuples_only on\n"
            "SELECT accession FROM lagmatrix.filing_coverage;\n").splitlines() if ln.strip()}
    print(f"  {len(tickers)} filers, {args.filings} filing(s) each; "
          f"{len(done)} filings already walked")

    tot = 0
    for i, tic in enumerate(tickers, 1):
        if tic not in by_tic:
            continue
        cik = str(by_tic[tic]["cik_str"]).zfill(10)
        try:
            sub = json.loads(http(f"https://data.sec.gov/submissions/CIK{cik}.json"))
        except Exception as e:
            print(f"    {i}/{len(tickers)} {tic:<6} submissions: {type(e).__name__}")
            continue
        r = sub["filings"]["recent"]
        tenks = [(r["accessionNumber"][j], r["filingDate"][j], r["primaryDocument"][j])
                 for j in range(len(r["form"])) if r["form"][j] == "10-K"][: args.filings]
        found = 0
        for acc, fdate, doc in tenks:
            if acc in done:
                continue
            a = acc.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{a}/{doc}"
            try:
                text = clean(http(url))
            except Exception:
                continue
            time.sleep(SEC_PAUSE)
            rows, seen = [], set()
            for mm in NAME_RE.finditer(text):
                nm = mm.group(1)
                cp = name_to_tic.get(nm)
                if not cp or cp == tic:
                    continue
                # multi-word names are self-disambiguating; single words need a
                # corporate designator immediately after them
                if " " not in nm and not DESIGNATOR.match(text[mm.end(): mm.end() + 24]):
                    continue
                lo = max(0, mm.start() - WINDOW)
                w = text[lo: mm.end() + WINDOW]
                if not CUSTOMER_CTX.search(w):
                    continue
                rel = label(w, mm.start() - lo)
                if rel != "customer":
                    continue
                key = (cp, w[:80])
                if key in seen:
                    continue
                seen.add(key)
                pct = PCT.search(w)
                rows.append([tic, cik, acc, fdate, "10-K", nm, cp, rel, "heuristic",
                             pct.group(1) if pct else "", w[:4000]])
            if rows:
                buf = io.StringIO()
                csv.writer(buf, lineterminator="\n").writerows(rows)
                psql("BEGIN;\nCREATE TEMP TABLE s (LIKE lagmatrix.filing_mention "
                     "INCLUDING DEFAULTS) ON COMMIT DROP;\n"
                     "COPY s (filer_ticker, filer_cik, accession, filing_date, form, "
                     "counterparty, cp_ticker, relation, confidence, pct_revenue, passage) "
                     "FROM STDIN WITH (FORMAT csv);\n" + buf.getvalue() + "\\.\n"
                     "INSERT INTO lagmatrix.filing_mention (filer_ticker, filer_cik, "
                     "accession, filing_date, form, counterparty, cp_ticker, relation, "
                     "confidence, pct_revenue, passage) SELECT filer_ticker, filer_cik, "
                     "accession, filing_date, form, counterparty, cp_ticker, relation, "
                     "confidence, pct_revenue, passage FROM s "
                     "ON CONFLICT DO NOTHING;\nCOMMIT;\n")
            psql("INSERT INTO lagmatrix.filing_coverage (accession, filer_ticker, "
                 f"filing_date, n_mentions) VALUES ('{acc}', '{tic}', '{fdate}', {len(rows)}) "
                 "ON CONFLICT (accession) DO UPDATE SET n_mentions = EXCLUDED.n_mentions;\n")
            found += len(rows)
        tot += found
        if found or i % 25 == 0:
            print(f"    {i}/{len(tickers)} {tic:<6} {found} customer edges")

    print(f"\n  extracted {tot} customer edges this run")
    print("  " + psql("\\pset tuples_only on\n"
          "SELECT 'store: '||count(*)||' mentions, '||count(DISTINCT filer_ticker)||"
          "' filers, '||count(DISTINCT cp_ticker)||' counterparties' "
          "FROM lagmatrix.filing_mention;\n").strip())


if __name__ == "__main__":
    main()
