"""D-63: does the SHIPPED feature predict, on synthetic candidates?

D-31 and D-33 tested `max abs(neighbour z) - abs(candidate z)`. The pipeline
computes something else entirely: an independence-weighted, direction-matched
sum of evidence. Q-31 recorded that this — the project's distinctive idea — has
never been evaluated.

So this script does not reimplement anything. It calls the production nodes in
order and reads the verdict they produce:

    retrieve_neighbourhood -> leader_state -> fuse_evidence -> assess

Design is fixed by D-63 and must not be adjusted after seeing output. In
particular: returns are market-excess, standard errors are clustered by date,
and a difference below 10bp is declared not meaningful regardless of p.

Usage:  uv run python scripts/experiment3.py [--bars data/bars-5y.parquet]
                                             [--sample 20000] [--seed 20260906]
"""

from __future__ import annotations

import argparse
import csv
import math
from datetime import date

import numpy as np
import pandas as pd

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.assessor import assess
from lagmatrix.graph.nodes.context_fusion import fuse_evidence
from lagmatrix.graph.nodes.graph_retriever import retrieve_neighbourhood
from lagmatrix.graph.nodes.leader_state import leader_state

TRAIL = 60
FWD = 2               # sessions; matches D-33's corrected horizon
HORIZONS = (2, 5, 10, 21)  # D-66 ladder, computed in one pass
MIN_EFFECT_BP = 10.0  # D-63's declared economic threshold


class _Rt:
    def __init__(self, ctx):
        self.context = ctx


def verdict_for(c: Candidate, ctx) -> tuple[str, float] | None:
    """Run the production nodes in order. No reimplementation (Q-31)."""
    rt = _Rt(ctx)
    st: dict = {"candidates": [c]}
    st |= retrieve_neighbourhood(st, rt)
    st |= leader_state(st, rt)
    st |= fuse_evidence(st, rt)
    st |= assess(st)
    a = st.get("assessments") or []
    return (a[0].verdict, a[0].effective_evidence) if a else None


def clustered_se(values: np.ndarray, groups: np.ndarray) -> float:
    """SE of the mean, clustered by group (D-63): rows sharing a date are not
    independent, and treating them as such is how a panel fabricates power."""
    uniq = np.unique(groups)
    means = np.array([values[groups == g].mean() for g in uniq])
    counts = np.array([(groups == g).sum() for g in uniq])
    w = counts / counts.sum()
    overall = (w * means).sum()
    var = ((w**2) * ((means - overall) ** 2)).sum() * len(uniq) / max(len(uniq) - 1, 1)
    return math.sqrt(var)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="data/bars-5y.parquet")
    ap.add_argument("--sample", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260906)
    ap.add_argument("--out", default="data/results3.csv")
    ap.add_argument("--top-liquid", type=int, default=0,
                    help="restrict CANDIDATES to the N most liquid non-fund names "
                         "(D-64); neighbourhoods still use the full pool")
    args = ap.parse_args()

    bars = pd.read_parquet(args.bars)
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    # NO global coverage filter. Requiring 90% coverage over the whole frame is
    # harmless on a short window and a survivorship filter on a long one: over
    # 2016-2026 it drops every post-2016 IPO (ABNB, PLTR, HOOD, COIN, CRWD,
    # SNOW, UBER...) and keeps only decade-long survivors. Measured cost of that
    # mistake: on identical 2021-2026 dates the gap went +10.4bp (D-64's
    # universe) -> +41.1bp (decade survivors). The nodes already enforce
    # coverage per trailing window, which is the point-in-time place to do it.
    sessions = closes.index
    rets = closes.pct_change()
    print(f"  grid: {closes.shape[0]} sessions x {closes.shape[1]} symbols "
          f"({sessions.min().date()} -> {sessions.max().date()})")

    with open("data/excluded-etfs.csv") as fh:
        funds = frozenset(r["symbol"] for r in csv.DictReader(fh))

    # market-excess: equal-weighted mean across the sampled universe, per session
    mkt = rets.mean(axis=1)

    usable = range(TRAIL, len(sessions) - FWD)
    syms = list(closes.columns)
    if args.top_liquid:
        recent = pd.read_parquet("data/bars.parquet")
        dv = recent.groupby("symbol").dollar_vol.median().sort_values(ascending=False)
        ranked = [s for s in dv.index if s not in funds and s in closes.columns]
        syms = ranked[: args.top_liquid]
        print(f"  candidates restricted to top {len(syms)} by $volume "
              f"(min ${dv[syms[-1]]/1e9:.2f}B); neighbourhoods still use all "
              f"{closes.shape[1]} columns")
    rng = np.random.default_rng(args.seed)
    pairs = [(syms[rng.integers(len(syms))], int(rng.choice(list(usable))))
             for _ in range(args.sample)]
    pairs = sorted(set(pairs))
    print(f"  sampled {len(pairs)} distinct (symbol, date) pairs")

    rows = []
    for n, (sym, ti) in enumerate(pairs, 1):
        if n % 2000 == 0:
            print(f"    {n}/{len(pairs)}")
        d = sessions[ti].date()
        # the node picks the first session strictly after as_of; give it the day before
        as_of = sessions[ti - 1].date() if isinstance(d, date) else d
        c = Candidate(symbol=sym, direction="up", as_of=as_of, origin="scan")
        ctx = LagMatrixContext(closes=closes, signal_universe=frozenset(),
                               excluded_symbols=funds, trail=TRAIL)
        try:
            got = verdict_for(c, ctx)
        except Exception:
            continue
        if got is None:
            continue
        verdict, eff = got
        p0 = closes[sym].iloc[ti]
        if not np.isfinite(p0) or p0 <= 0:
            continue
        row = {"symbol": sym, "date": str(sessions[ti].date()), "ti": ti,
               "verdict": verdict, "effective_evidence": eff}
        ok = False
        for h in HORIZONS:
            if ti + h >= len(sessions):
                row[f"excess_{h}"] = np.nan
                continue
            p1 = closes[sym].iloc[ti + h]
            if not np.isfinite(p1):
                row[f"excess_{h}"] = np.nan
                continue
            row[f"excess_{h}"] = (p1 / p0 - 1.0) - mkt.iloc[ti + 1: ti + 1 + h].sum()
            ok = ok or h == FWD
        row["excess_fwd"] = row.get(f"excess_{FWD}", np.nan)
        row["raw_fwd"] = np.nan
        if not np.isfinite(row["excess_fwd"]):
            continue
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)
    print(f"\n  evaluable: {len(df)} over {df.date.nunique()} distinct dates")
    print(f"  verdicts: {df.verdict.value_counts().to_dict()}\n")

    g = {k: v for k, v in df.groupby("verdict")}
    for k in ("corroborated", "neutral", "contradicted"):
        if k in g:
            d = g[k]
            print(f"  {k:<13} n={len(d):>6}  mean excess {d.excess_fwd.mean()*1e4:+7.2f} bp"
                  f"   hit {(d.excess_fwd > 0).mean():.1%}")

    if "corroborated" in g and "contradicted" in g:
        a, b = g["corroborated"], g["contradicted"]
        diff = a.excess_fwd.mean() - b.excess_fwd.mean()
        both = pd.concat([a.assign(s=1), b.assign(s=-1)])
        se = clustered_se(both.excess_fwd.values * both.s.values, both.date.values)
        z = diff / se if se > 0 else float("nan")
        print("\n  PRIMARY TEST (D-63)")
        print(f"    corroborated - contradicted : {diff*1e4:+.2f} bp")
        print(f"    SE, clustered by date       : {se*1e4:.2f} bp  "
              f"({both.date.nunique()} clusters)")
        print(f"    z                           : {z:+.2f}")
        meaningful = abs(diff) * 1e4 >= MIN_EFFECT_BP
        sig = abs(z) >= 1.96
        print(f"    >= {MIN_EFFECT_BP}bp threshold        : {'yes' if meaningful else 'NO'}")
        print(f"    |z| >= 1.96                 : {'yes' if sig else 'no'}")
        print(f"    -> {'EFFECT' if (meaningful and sig) else 'NULL'}")


if __name__ == "__main__":
    main()
