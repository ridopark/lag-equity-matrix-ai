"""How much does using today's SIC instead of the filing-date SIC bias D-137?

Q-62: `data.sec.gov/submissions/CIK*.json` returns only a company's CURRENT
`sic`, so `scripts/load_sectors.py` joins today's classification onto
historical pairs. That is a point-in-time violation (D-16) of unknown size.

This measures the size rather than assuming it. SEC's Financial Statement Data
Sets publish `sub.txt` per quarter with (cik, sic, filed) -- the SIC as it stood
when that filing was made -- so an old quarter gives a genuine point-in-time
label. The same-sector retention lift is then computed twice over the identical
pairs, once with each label set, and the difference IS the bias.

Usage:  uv run python scripts/measure_sic_drift.py [--quarter 2018q1]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from experiment_lag_matrix import split_at_boundary, standardise  # noqa: E402

from lagmatrix.comovement import _IMPLAUSIBLE_RETURN_CUTOFF  # noqa: E402
from lagmatrix.config import load_settings  # noqa: E402

UA = {"User-Agent": "lag-equity-matrix-ai research ridopark@gmail.com"}
BAND = (0.4, 0.5)
DATASET = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{q}.zip"


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180).read()


def historical_sic(quarter: str) -> dict[str, str]:
    """cik -> SIC as recorded on that quarter's filings."""
    z = zipfile.ZipFile(io.BytesIO(_get(DATASET.format(q=quarter))))
    out: dict[str, str] = {}
    with z.open("sub.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter="\t"):
            if row.get("sic"):
                out[str(int(row["cik"]))] = str(row["sic"])
    return out


def lift(same: np.ndarray, retained: np.ndarray, seed: int = 0) -> tuple[float, float, float]:
    d = (retained[same].mean() - retained[~same].mean()) * 100
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(3000):
        b = rng.integers(0, len(retained), len(retained))
        if same[b].sum() > 5 and (~same[b]).sum() > 5:
            bs.append(retained[b][same[b]].mean() - retained[b][~same[b]].mean())
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return d, lo * 100, hi * 100


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", default="2018q1", help="SEC dataset quarter, e.g. 2018q1")
    args = ap.parse_args()

    s = load_settings()
    from arango import ArangoClient

    db = ArangoClient(hosts=s.arango_url).db(
        s.arango_db, username=s.arango_user,
        password=Path("~/.lagmatrix-arango-pw").expanduser().read_text().strip(),
    )
    current = {r["symbol"]: str(r["sic"]) for r in db.aql.execute(
        "FOR v IN equity FILTER v.sic != null RETURN {symbol: v._key, sic: v.sic}")}
    tickers = json.loads(_get("https://www.sec.gov/files/company_tickers.json"))
    to_cik = {v["ticker"]: str(v["cik_str"]) for v in tickers.values()}
    hist = historical_sic(args.quarter)
    pit = {sym: hist[to_cik[sym]] for sym in current if sym in to_cik and to_cik[sym] in hist}
    print(f"  symbols with both {args.quarter} and current SIC: {len(pit):,}")

    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change().iloc[1:]
    rets = rets.where(rets.abs() <= _IMPLAUSIBLE_RETURN_CUTOFF)          # D-100
    d_raw, v_raw = split_at_boundary(rets)
    zd, a_ = standardise(d_raw)
    zv, b_ = standardise(v_raw)
    common = [c for c in a_ if c in set(b_)]
    di = {c: i for i, c in enumerate(a_)}
    vi = {c: i for i, c in enumerate(b_)}
    zd = zd[:, [di[c] for c in common]]
    zv = zv[:, [vi[c] for c in common]]
    cd = (zd.T @ zd) / len(zd)
    cv = (zv.T @ zv) / len(zv)
    iu, ju = np.triu_indices(len(common), k=1)
    ad, v = np.abs(cd[iu, ju]), np.abs(cv[iu, ju])
    lo_b, hi_b = BAND
    band = (ad >= lo_b) & (ad < hi_b)
    sym = np.array(common)
    li, lj = iu[band], ju[band]
    dated = np.array([sym[i] in pit and sym[j] in pit for i, j in zip(li, lj, strict=True)])
    retained = (v[band] >= lo_b)[dated]
    li, lj = li[dated], lj[dated]
    print(f"  band pairs with both legs dated: {len(retained):,}\n")

    results = {}
    for label, table in (("point-in-time", pit), ("current", current)):
        same = np.array([table[sym[i]][:2] == table[sym[j]][:2]
                         for i, j in zip(li, lj, strict=True)])
        d, lo, hi = lift(same, retained)
        results[label] = d
        print(f"  {label:<14} SIC: same-sector {same.mean() * 100:4.1f}% of pairs   "
              f"retained {retained[same].mean() * 100:.1f}% vs "
              f"{retained[~same].mean() * 100:.1f}%   lift {d:+.1f}pp CI [{lo:+.1f},{hi:+.1f}]")
    bias = results["current"] - results["point-in-time"]
    print(f"\n  bias from using today's labels: {bias:+.2f}pp "
          f"({'inflates' if bias > 0 else 'deflates'} the measured effect)")


if __name__ == "__main__":
    main()
