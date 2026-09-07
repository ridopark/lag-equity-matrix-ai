"""D-73: does Apple's move lead its suppliers? Answers Q-34.

The first DIRECTED edge the project has. Correlation is symmetric (D-59/D-60
measured lag 0) and co-mention undirected; a 10-K disclosure is neither, because
QRVO names Apple and Apple never names QRVO.

Design fixed by D-73 before any relationship was examined, and not adjustable
after seeing output. In particular: h=1 only (h=2 and h=5 were excluded in
advance on power, not discarded after trying), the |b| >= 0.02 economic
threshold, and the per-year heterogeneity check that D-71 made mandatory.
"""

from __future__ import annotations

import argparse
import math
import subprocess

import numpy as np
import pandas as pd

HOST = "ridopark@192.168.10.123"
PSQL = "kubectl -n copytrade exec -i postgres-0 -- psql -U temporal -d orchestrator -q -t -A"
MIN_SLOPE = 0.02      # D-73's declared economic threshold
CUSTOMER = "AAPL"


def supplier_first_filing(customer: str) -> dict[str, pd.Timestamp]:
    sql = ("SELECT supplier, min(filing_date) FROM lagmatrix.supply_edge "
           f"WHERE customer='{customer}' GROUP BY supplier;\n")
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, PSQL],
                         input=sql, capture_output=True, text=True, timeout=300).stdout
    return {ln.split("|")[0]: pd.Timestamp(ln.split("|")[1], tz="UTC")
            for ln in out.splitlines() if "|" in ln}


def ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, int]:
    """slope, its standard error, n. Plain OLS: the one-session lag means the
    observations do not overlap, which is why D-73 chose h=1."""
    n = len(x)
    xc = x - x.mean()
    b = float((xc * (y - y.mean())).sum() / (xc**2).sum())
    resid = y - (y.mean() + b * xc)
    se = math.sqrt((resid**2).sum() / (n - 2) / (xc**2).sum())
    return b, se, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", default=CUSTOMER)
    ap.add_argument("--bars", default="data/bars-10y.parquet")
    args = ap.parse_args()

    first = supplier_first_filing(args.customer)
    bars = pd.read_parquet(args.bars)
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change()
    mkt = rets.mean(axis=1)
    start = min(first.values())
    idx = rets.index[rets.index >= start]

    live = {s: d for s, d in first.items() if s in rets.columns}
    print(f"  customer {args.customer}: {len(live)} suppliers with prices")
    print(f"  window {idx.min().date()} -> {idx.max().date()}  ({len(idx)} sessions)")

    # point-in-time membership: a supplier joins only after its naming filing
    port = []
    for d in idx:
        members = [s for s, fd in live.items()
                   if d >= fd and np.isfinite(rets.at[d, s])]
        port.append(rets.loc[d, members].mean() - mkt.at[d] if members else np.nan)
    sup = pd.Series(port, index=idx)
    cust = rets[args.customer].reindex(idx) - mkt.reindex(idx)

    df = pd.DataFrame({"cust": cust, "sup_fwd": sup.shift(-1)}).dropna()
    b, se, n = ols(df.cust.values, df.sup_fwd.values)
    z = b / se
    print(f"\n  PRIMARY (D-73): supplier_excess(t+1) = a + b * {args.customer}_excess(t)")
    print(f"    n = {n}")
    print(f"    b  = {b:+.4f}   SE {se:.4f}   z = {z:+.2f}")
    print(f"    95% CI [{b-1.96*se:+.4f}, {b+1.96*se:+.4f}]")
    print(f"    MDE (80% power) |b| >= {2.8*se:.4f}")
    print(f"    a 1% {args.customer} move implies {b*100:+.1f} bp on the portfolio")
    ok_econ = abs(b) >= MIN_SLOPE
    ok_stat = abs(z) >= 1.96
    print(f"    |b| >= {MIN_SLOPE} threshold : {'yes' if ok_econ else 'NO'}")
    print(f"    |z| >= 1.96                : {'yes' if ok_stat else 'no'}")
    print(f"    -> {'EFFECT' if (ok_econ and ok_stat) else 'NULL'}")

    print("\n  MANDATORY SECONDARY (D-71): is the slope stable across years?")
    df["year"] = df.index.year
    est, ses = [], []
    for y, g in df.groupby("year"):
        if len(g) < 60:
            continue
        by, sy, ny = ols(g.cust.values, g.sup_fwd.values)
        est.append(by)
        ses.append(sy)
        print(f"    {y}  b {by:+.4f}  SE {sy:.4f}  z {by/sy:+5.2f}  n={ny}")
    est, ses = np.array(est), np.array(ses)
    w = 1 / ses**2
    mu = (w * est).sum() / w.sum()
    Q = (w * (est - mu) ** 2).sum()
    dfree = len(est) - 1
    I2 = max(0.0, (Q - dfree) / Q) if Q > 0 else 0.0
    print(f"    inverse-variance pooled b = {mu:+.4f}   (naive pooled {b:+.4f})")
    print(f"    Cochran Q = {Q:.1f} on {dfree} df    I^2 = {I2:.0%}")
    print(f"    -> {'STABLE' if I2 < 0.5 else 'HETEROGENEOUS — the naive SE understates'}")


if __name__ == "__main__":
    main()
