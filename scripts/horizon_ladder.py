"""D-66: the decay profile of the shipped feature across holding periods.

Reads the multi-horizon output of experiment3.py and reports the corroborated
minus contradicted gap at each horizon, with standard errors clustered on
NON-OVERLAPPING blocks of length = horizon. Consecutive dates share all but one
day of a 21-session forward return; clustering by date does nothing about that,
and unclustered errors at long horizons are meaningless.

D-66 declared in advance that no horizon here can be significant: expected effect
and SE both scale as h, so the ratio is invariant. Read the shape, not the stars.
"""

from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

HORIZONS = (2, 5, 10, 21)


def block_clustered_se(v: np.ndarray, blocks: np.ndarray) -> float:
    uniq = np.unique(blocks)
    means = np.array([v[blocks == b].mean() for b in uniq])
    counts = np.array([(blocks == b).sum() for b in uniq])
    w = counts / counts.sum()
    overall = (w * means).sum()
    var = ((w**2) * ((means - overall) ** 2)).sum() * len(uniq) / max(len(uniq) - 1, 1)
    return math.sqrt(var)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="data/results5.csv")
    args = ap.parse_args()
    d = pd.read_csv(args.results)

    print(f"  {len(d):,} candidate-dates, {d.date.nunique()} sessions\n")
    print(f"  {'horizon':>7} {'n':>6} {'blocks':>7} {'corrob':>9} {'contra':>9} "
          f"{'gap bp':>9} {'SE':>7} {'z':>6}   per-session")
    for h in HORIZONS:
        col = f"excess_{h}"
        if col not in d.columns:
            continue
        g = d[d[col].notna() & d.verdict.isin(["corroborated", "contradicted"])].copy()
        if g.empty:
            continue
        g["blk"] = g.ti // h                      # non-overlapping blocks
        g["s"] = np.where(g.verdict == "corroborated", 1, -1)
        a = g[g.s == 1][col].mean()
        b = g[g.s == -1][col].mean()
        se = block_clustered_se(g[col].values * g.s.values, g.blk.values)
        gap = a - b
        z = gap / se if se > 0 else float("nan")
        print(f"  {h:>7} {len(g):>6} {g.blk.nunique():>7} {a*1e4:>+9.2f} {b*1e4:>+9.2f} "
              f"{gap*1e4:>+9.2f} {se*1e4:>7.2f} {z:>+6.2f}   {gap*1e4/h:>+6.2f} bp/session")

    print("\n  The last column is what decides holding period: if it falls, the")
    print("  effect is front-loaded and holding longer adds variance for nothing.")
    print("  No z here can be significant — D-66 established that before the run.")


if __name__ == "__main__":
    main()
