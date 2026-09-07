"""D-74: pool D-73's test across every supply chain with >= 3 suppliers.

Identical design to D-73 -- one-session lag, market-excess both sides,
point-in-time membership, |b| >= 0.02 threshold -- changed only in population.

Errors are clustered by DATE. On any session every chain shares the same market
shock, so treating chains as independent would inflate the effective sample the
way D-71 caught after the fact. Declared before running, not discovered.
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
MIN_SUPPLIERS = 3


def chains() -> dict[str, dict[str, pd.Timestamp]]:
    sql = ("SELECT customer, supplier, min(filing_date) FROM lagmatrix.supply_edge "
           "GROUP BY customer, supplier;\n")
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, PSQL],
                         input=sql, capture_output=True, text=True, timeout=300).stdout
    d: dict[str, dict[str, pd.Timestamp]] = {}
    for ln in out.splitlines():
        parts = ln.split("|")
        if len(parts) == 3:
            d.setdefault(parts[0], {})[parts[1]] = pd.Timestamp(parts[2], tz="UTC")
    return d


def cluster_ols(x, y, groups):
    """slope with date-clustered standard error."""
    n = len(x)
    xc = x - x.mean()
    b = float((xc * (y - y.mean())).sum() / (xc**2).sum())
    resid = y - (y.mean() + b * xc)
    sxx = (xc**2).sum()
    meat = 0.0
    for g in np.unique(groups):
        m = groups == g
        meat += (xc[m] * resid[m]).sum() ** 2
    se = math.sqrt(meat) / sxx
    return b, se, n, len(np.unique(groups))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="data/bars-10y.parquet")
    args = ap.parse_args()

    ch = chains()
    bars = pd.read_parquet(args.bars)
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change()
    mkt = rets.mean(axis=1)

    usable = {c: s for c, s in ch.items()
              if c in rets.columns
              and len({k for k in s if k in rets.columns}) >= MIN_SUPPLIERS}
    print(f"  {len(ch)} chains total; {len(usable)} with >= {MIN_SUPPLIERS} priced suppliers")

    frames = []
    for cust, sups in usable.items():
        sups = {s: d for s, d in sups.items() if s in rets.columns}
        start = min(sups.values())
        idx = rets.index[rets.index >= start]
        vals = []
        for d in idx:
            members = [s for s, fd in sups.items() if d >= fd and np.isfinite(rets.at[d, s])]
            vals.append(rets.loc[d, members].mean() - mkt.at[d] if members else np.nan)
        sup = pd.Series(vals, index=idx)
        cust_ex = rets[cust].reindex(idx) - mkt.reindex(idx)
        f = pd.DataFrame({"cust": cust_ex, "sup_fwd": sup.shift(-1)}).dropna()
        f["customer"] = cust
        frames.append(f)
        print(f"    {cust:<6} {len(sups)} suppliers, {len(f)} sessions")

    df = pd.concat(frames)
    df["date"] = df.index
    b, se, n, nclust = cluster_ols(df.cust.values, df.sup_fwd.values,
                                   df.date.values.astype("datetime64[D]"))
    z = b / se
    print(f"\n  PRIMARY (D-74): pooled across {len(usable)} chains")
    print(f"    n = {n:,} chain-days over {nclust} date clusters")
    print(f"    b  = {b:+.4f}   clustered SE {se:.4f}   z = {z:+.2f}")
    print(f"    95% CI [{b-1.96*se:+.4f}, {b+1.96*se:+.4f}]")
    print(f"    MDE (80% power) |b| >= {2.8*se:.4f}   vs threshold {MIN_SLOPE}")
    print(f"    a 1% customer move implies {b*100:+.1f} bp on its suppliers")
    ok = abs(b) >= MIN_SLOPE and abs(z) >= 1.96
    powered = 2.8 * se < MIN_SLOPE
    note = ("informative: MDE is below the economic threshold" if powered
            else "UNDERPOWERED: MDE exceeds the economic threshold")
    print(f"    -> {'EFFECT' if ok else 'NULL'}   ({note})")

    print("\n  MANDATORY SECONDARY (D-71): stability across years")
    df["year"] = df.index.year
    est, ses = [], []
    for y, g in df.groupby("year"):
        if len(g) < 200:
            continue
        by, sy, _, _ = cluster_ols(g.cust.values, g.sup_fwd.values,
                                   g.date.values.astype("datetime64[D]"))
        est.append(by)
        ses.append(sy)
        print(f"    {y}  b {by:+.4f}  SE {sy:.4f}  z {by/sy:+5.2f}  n={len(g):,}")
    est, ses = np.array(est), np.array(ses)
    w = 1 / ses**2
    mu = (w * est).sum() / w.sum()
    Q = float((w * (est - mu) ** 2).sum())
    dfree = len(est) - 1
    I2 = max(0.0, (Q - dfree) / Q) if Q > 0 else 0.0
    print(f"    inverse-variance pooled b = {mu:+.4f}   (naive {b:+.4f})")
    print(f"    Cochran Q = {Q:.1f} on {dfree} df    I^2 = {I2:.0%}")
    print(f"    -> {'STABLE' if I2 < 0.5 else 'HETEROGENEOUS — naive SE understates'}")


if __name__ == "__main__":
    main()
