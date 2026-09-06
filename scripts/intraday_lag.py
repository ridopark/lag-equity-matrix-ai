"""The pre-registered intraday lead-lag test (D-59).

Runs the design fixed before any minute bar was fetched. No parameter here was
chosen after seeing data; changing one and re-running invalidates the result.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

MAX_LAG = 30          # minutes each side
MIN_OBS = 120         # a candidate-date needs this many aligned minutes to count


def series(px: pd.DataFrame, syms: list[str]) -> pd.Series | None:
    cols = [s for s in syms if s in px.columns]
    if not cols:
        return None
    r = np.log(px[cols]).diff()
    return r.mean(axis=1)


def argmax_lag(lead: pd.Series, follow: pd.Series) -> tuple[int, float] | None:
    """argmax_k corr(lead(t-k), follow(t)); k>0 means `lead` leads."""
    best_k, best_r = None, -np.inf
    for k in range(-MAX_LAG, MAX_LAG + 1):
        a, b = lead.shift(k), follow
        m = a.notna() & b.notna()
        if m.sum() < MIN_OBS:
            continue
        r = a[m].corr(b[m])
        if np.isfinite(r) and r > best_r:
            best_k, best_r = k, r
    return (best_k, best_r) if best_k is not None else None


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--nofunds", action="store_true",
                    help="use the fund-excluded neighbourhoods (D-60)")
    args = ap.parse_args()
    suffix = "-nofunds" if args.nofunds else ""
    bars = pd.read_parquet(f"data/minute{suffix}.parquet")
    plan = json.loads(open(f"data/minute-plan{suffix}.json").read())

    rows = []
    for day, cands in sorted(plan.items()):
        px = bars[bars.timestamp.dt.date.astype(str) == day].pivot_table(
            index="timestamp", columns="symbol", values="close")
        if px.empty:
            continue
        for sym, sets in cands.items():
            if sym not in px.columns:
                continue
            cand = np.log(px[sym]).diff()
            for label, group in (("real", sets["neighbours"]), ("control", sets["control"])):
                nb = series(px, group)
                if nb is None:
                    continue
                fwd = argmax_lag(nb, cand)      # neighbourhood leads candidate
                rev = argmax_lag(cand, nb)      # candidate leads neighbourhood
                if fwd is None or rev is None:
                    continue
                rows.append({"day": day, "symbol": sym, "set": label,
                             "lag": fwd[0], "rho": fwd[1],
                             "rev_lag": rev[0], "rev_rho": rev[1],
                             "n_syms": len([s for s in group if s in px.columns])})

    df = pd.DataFrame(rows)
    df.to_csv(f"data/intraday-lag{suffix}.csv", index=False)
    real = df[df.set == "real"]
    ctrl = df[df.set == "control"]

    print(f"  candidate-dates analysed : {len(real)} real, {len(ctrl)} control")
    print()
    for name, d in (("REAL neighbourhood", real), ("CONTROL (liquidity-matched)", ctrl)):
        print(f"  {name}")
        print(f"    median argmax lag : {d.lag.median():+.1f} min")
        print(f"    mean   argmax lag : {d.lag.mean():+.2f} min")
        print(f"    lag > 0           : {(d.lag > 0).mean():.1%}"
              f"   lag == 0: {(d.lag == 0).mean():.1%}")
        print(f"    median peak rho   : {d.rho.median():.3f}")
        print()

    # pre-committed decision rule
    paired = real.merge(ctrl, on=["day", "symbol"], suffixes=("_r", "_c"))
    diff = paired.lag_r - paired.lag_c
    pos, neg = int((diff > 0).sum()), int((diff < 0).sum())
    from math import comb
    n = pos + neg
    p = sum(comb(n, i) for i in range(pos, n + 1)) / 2**n * 2 if n else 1.0
    p = min(p, 1.0)
    gap = real.lag.median() - ctrl.lag.median()

    print("  PRE-COMMITTED DECISION RULE (D-59)")
    print(f"    real median - control median : {gap:+.1f} min   (need >= +1.0)")
    print(f"    paired sign test             : {pos} up / {neg} down, p = {p:.3f}  (need < 0.05)")
    verdict = "LEAD-LAG PRESENT" if (gap >= 1.0 and p < 0.05) else "NULL — no intraday lead-lag"
    print(f"    -> {verdict}")
    print()
    print("  SYMMETRY CHECK (a real diffusion effect is asymmetric)")
    print(f"    neighbourhood leads candidate : median {real.lag.median():+.1f} min")
    print(f"    candidate leads neighbourhood : median {real.rev_lag.median():+.1f} min")


if __name__ == "__main__":
    main()
