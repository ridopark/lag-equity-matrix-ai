"""D-75: does the news co-mention edge lead the candidate? Last untested edge.

Mirrors D-74 exactly, changing only the edge definition: PMI over articles
tagging <= 8 symbols (D-70) instead of a supply-chain disclosure.

Declared in D-75 before running, and not negotiable afterwards: MDE is ~0.06
against a 0.02 economic threshold, so this bounds a LARGE effect and cannot
resolve a tradeable one. A null here means "no large effect", never "no effect".

Point-in-time: neighbours for month M are computed only from articles published
strictly before M begins. The news_comention view is deliberately not
materialised so this bound cannot be forgotten.
"""

from __future__ import annotations

import argparse
import math
import subprocess

import numpy as np
import pandas as pd

HOST = "ridopark@192.168.10.123"
PSQL = "kubectl -n copytrade exec -i postgres-0 -- psql -U temporal -d orchestrator -q -t -A"
MIN_SLOPE = 0.02
TOPK = 20
BREADTH = 8
MIN_PAIR = 20
MIN_FREQ = 60


def pmi_neighbours(seeds: list[str], before: str) -> dict[str, list[str]]:
    """Top-k PMI peers per seed, using only articles published before `before`."""
    inlist = ",".join(f"'{s}'" for s in seeds)
    sql = f"""
WITH ok AS (
  SELECT s.article_id FROM lagmatrix.news_symbol s
  JOIN lagmatrix.news_article a ON a.id = s.article_id
  WHERE a.created_at < '{before}'
  GROUP BY s.article_id HAVING count(*) <= {BREADTH}),
tot AS (SELECT count(*)::numeric n FROM ok),
freq AS (SELECT s.symbol, count(*)::numeric n FROM lagmatrix.news_symbol s
         JOIN ok USING (article_id) GROUP BY 1 HAVING count(*) >= {MIN_FREQ}),
pair AS (SELECT symbol_a a, symbol_b b, count(*)::numeric n
         FROM lagmatrix.news_comention c JOIN ok ON ok.article_id = c.article_id
         WHERE c.created_at < '{before}'
           AND (symbol_a IN ({inlist}) OR symbol_b IN ({inlist}))
         GROUP BY 1,2 HAVING count(*) >= {MIN_PAIR})
SELECT CASE WHEN p.a IN ({inlist}) THEN p.a ELSE p.b END,
       CASE WHEN p.a IN ({inlist}) THEN p.b ELSE p.a END,
       round(ln((p.n/t.n)/((f1.n/t.n)*(f2.n/t.n)))::numeric, 4)
FROM pair p JOIN tot t ON true
JOIN freq f1 ON f1.symbol = p.a JOIN freq f2 ON f2.symbol = p.b;
"""
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, PSQL], input=sql,
                         capture_output=True, text=True, timeout=900).stdout
    rows = [ln.split("|") for ln in out.splitlines() if ln.count("|") == 2]
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["seed", "peer", "pmi"])
    df["pmi"] = df.pmi.astype(float)
    return {s: g.nlargest(TOPK, "pmi").peer.tolist() for s, g in df.groupby("seed")}


def cluster_ols(x, y, groups):
    xc = x - x.mean()
    b = float((xc * (y - y.mean())).sum() / (xc**2).sum())
    resid = y - (y.mean() + b * xc)
    sxx = (xc**2).sum()
    meat = sum((xc[groups == g] * resid[groups == g]).sum() ** 2 for g in np.unique(groups))
    return b, math.sqrt(meat) / sxx, len(x), len(np.unique(groups))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="data/bars-10y.parquet")
    args = ap.parse_args()

    seeds = sorted(pd.read_csv("data/fires.csv").ticker.unique())
    bars = pd.read_parquet(args.bars)
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change()
    mkt = rets.mean(axis=1)
    idx = rets.index[rets.index >= pd.Timestamp("2017-01-01", tz="UTC")]

    months = sorted({(d.year, d.month) for d in idx})
    print(f"  {len(seeds)} seeds, {len(months)} months, neighbours recomputed monthly")

    recs = []
    for k, (yy, mm) in enumerate(months):
        asof = f"{yy}-{mm:02d}-01"
        nb = pmi_neighbours(seeds, asof)
        if not nb:
            continue
        days = [d for d in idx if d.year == yy and d.month == mm]
        for seed, peers in nb.items():
            if seed not in rets.columns:
                continue
            p = [x for x in peers if x in rets.columns]
            if len(p) < 5:
                continue
            for d in days:
                nxt = idx[idx > d]
                if len(nxt) == 0:
                    continue
                x = rets.loc[d, p].mean() - mkt.at[d]
                y = rets.at[nxt[0], seed] - mkt.at[nxt[0]]
                if np.isfinite(x) and np.isfinite(y):
                    recs.append((d, seed, x, y))
        if (k + 1) % 24 == 0:
            print(f"    {k+1}/{len(months)} months, {len(recs):,} obs", flush=True)

    df = pd.DataFrame(recs, columns=["date", "seed", "x", "y"])
    b, se, n, nc = cluster_ols(df.x.values, df.y.values,
                               df.date.values.astype("datetime64[D]"))
    z = b / se
    print("\n  PRIMARY (D-75): candidate_excess(t+1) = a + b * pmi_neighbours_excess(t)")
    print(f"    n = {n:,} seed-days over {nc} date clusters, {df.seed.nunique()} seeds")
    print(f"    b  = {b:+.4f}   clustered SE {se:.4f}   z = {z:+.2f}")
    print(f"    95% CI [{b-1.96*se:+.4f}, {b+1.96*se:+.4f}]")
    print(f"    MDE {2.8*se:.4f}   (D-75 declared ~0.06; threshold {MIN_SLOPE})")
    print(f"    a 1% neighbour move implies {b*100:+.1f} bp on the candidate")
    big = abs(b) >= 2.8 * se
    print(f"    -> {'LARGE EFFECT' if (big and abs(z) >= 1.96) else 'no large effect'}"
          f"   (cannot resolve {MIN_SLOPE}; D-75 said so in advance)")

    print("\n  MANDATORY SECONDARY (D-71): stability across years")
    df["year"] = pd.to_datetime(df.date).dt.year
    est, ses = [], []
    for y, g in df.groupby("year"):
        if len(g) < 200:
            continue
        by, sy, _, _ = cluster_ols(g.x.values, g.y.values,
                                   g.date.values.astype("datetime64[D]"))
        est.append(by)
        ses.append(sy)
        print(f"    {y}  b {by:+.4f}  SE {sy:.4f}  z {by/sy:+5.2f}  n={len(g):,}")
    est, ses = np.array(est), np.array(ses)
    w = 1 / ses**2
    mu = float((w * est).sum() / w.sum())
    Q = float((w * (est - mu) ** 2).sum())
    dfree = len(est) - 1
    I2 = max(0.0, (Q - dfree) / Q) if Q > 0 else 0.0
    print(f"    inverse-variance pooled b = {mu:+.4f}   (naive {b:+.4f})")
    print(f"    Cochran Q = {Q:.1f} on {dfree} df    I^2 = {I2:.0%}")
    print(f"    -> {'STABLE' if I2 < 0.5 else 'HETEROGENEOUS'}")


if __name__ == "__main__":
    main()
