"""Arm B3: does a RICHER brief make Haiku reason better?

D-134 left Haiku at 0.6484 against a deterministic ceiling of 0.6678, with a
brief carrying three real numbers. This arm adds the four enrichments:

  finer stability  -- four quarter-windows instead of two halves (the single
                      biggest win: 0.6958 held-out vs 0.6654 for two halves)
  concentration    -- share of |co-movement| from the ten largest days
  liquidity        -- median dollar volume, as a population-calibrated quartile
  relatedness      -- co-mention/supply edge WHERE KNOWN; coverage is 2.0% and
                      0.1% of band pairs respectively, so its effect is not
                      measurable at this n. Carried because it was asked for,
                      reported as uninformative rather than silently dropped.

Deliberately absent: "discovery sessions", which is the same constant on every
pair and so carries no information (D-135).

Scored against BOTH deterministic baselines on the same held-out pairs.
Usage:  uv run python scripts/experiment_arm_b3.py --n 400
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from experiment_lag_matrix import split_at_boundary, standardise  # noqa: E402

from lagmatrix.adapters.llm import build_analyst_client  # noqa: E402
from lagmatrix.comovement import _IMPLAUSIBLE_RETURN_CUTOFF  # noqa: E402
from lagmatrix.config import load_settings  # noqa: E402
from lagmatrix.domain.models import QuantAnalystNote  # noqa: E402

BAND = (0.4, 0.5)
WINDOWS = 4


def auc(score: np.ndarray, y: np.ndarray) -> float:
    o = np.argsort(score, kind="mergesort")
    t = y[o]
    r = np.arange(1, len(t) + 1)
    p, q = t.sum(), (~t).sum()
    return float((r[t].sum() - p * (p + 1) / 2) / (p * q))


def _decile_table(x: np.ndarray, y: np.ndarray, label: str, fmt: str = ".3f") -> str:
    edges = np.quantile(x, np.linspace(0, 1, 11))
    out = []
    for i in range(10):
        s = (x >= edges[i]) & (x < edges[i + 1] if i < 9 else x <= edges[10])
        if s.sum():
            out.append(f"  {label} {edges[i]:{fmt}}-{edges[i + 1]:{fmt}}: "
                       f"{y[s].mean() * 100:.0f}% retained")
    return "\n".join(out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400, help="held-out pairs to score (PAID)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--comention", type=str, default="", help="csv: a,b,n,pmi")
    ap.add_argument("--supply", type=str, default="", help="csv: supplier,customer")
    args = ap.parse_args()

    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    dollar_vol = (bars.close * bars.volume).groupby(bars.symbol).median()
    rets = closes.pct_change().iloc[1:]
    rets = rets.where(rets.abs() <= _IMPLAUSIBLE_RETURN_CUTOFF)      # D-100
    d_raw, v_raw = split_at_boundary(rets)
    zd, a_ = standardise(d_raw)
    zv, b_ = standardise(v_raw)
    common = [c for c in a_ if c in set(b_)]
    di = {c: i for i, c in enumerate(a_)}
    vi = {c: i for i, c in enumerate(b_)}
    zd = zd[:, [di[c] for c in common]]
    zv = zv[:, [vi[c] for c in common]]
    n, T = len(common), len(zd)
    sym = np.array(common)

    cd = (zd.T @ zd) / T
    cv = (zv.T @ zv) / len(zv)
    iu, ju = np.triu_indices(n, k=1)
    d, v = cd[iu, ju], np.abs(cv[iu, ju])
    ad = np.abs(d)
    lo, hi = BAND
    band = (ad >= lo) & (ad < hi)
    li, lj = iu[band], ju[band]

    # --- the four enrichments, over band pairs only ------------------------
    edges = np.linspace(0, T, WINDOWS + 1).astype(int)
    w = np.empty((WINDOWS, band.sum()))
    for k in range(WINDOWS):
        seg = zd[edges[k]:edges[k + 1]]
        w[k] = np.abs(((seg.T @ seg) / len(seg))[li, lj])
    med_w, min_w = np.median(w, 0), w.min(0)

    contrib = np.abs(zd[:, li] * zd[:, lj])
    top10 = np.sort(contrib, axis=0)[-10:].sum(0) / np.maximum(contrib.sum(0), 1e-12)

    liq = np.log10(np.maximum(dollar_vol.reindex(sym).to_numpy(dtype=float), 1.0))
    min_liq = np.minimum(liq[li], liq[lj])

    pmi: dict[tuple[str, str], float] = {}
    if args.comention:
        c = pd.read_csv(args.comention, header=None, names=["a", "b", "n", "pmi"])
        for a, b, p in zip(c.a, c.b, c.pmi, strict=True):
            pmi[(a, b)] = pmi[(b, a)] = float(p)
    supply: set[tuple[str, str]] = set()
    if args.supply:
        s = pd.read_csv(args.supply, header=None, names=["s", "c"])
        for r in s.itertuples():
            supply.add((r.s, r.c))
            supply.add((r.c, r.s))

    # --- disjoint train / held-out -----------------------------------------
    pos = np.arange(band.sum())
    rng = np.random.default_rng(args.seed)
    rng.shuffle(pos)
    cut = len(pos) // 2
    train, pool = pos[:cut], pos[cut:]
    test = rng.choice(pool, size=min(args.n, len(pool)), replace=False)

    vb = v[band]
    tr_y = vb[train] >= lo
    base = tr_y.mean() * 100
    t_stab = _decile_table(med_w[train], tr_y, "typical quarter |corr|")
    t_conc = _decile_table(top10[train], tr_y, "top-10-day share")
    t_liq = _decile_table(min_liq[train], tr_y, "log10 median $volume", ".1f")
    print(f"train {len(train):,} pairs (curves fitted here)   "
          f"test {len(test):,} pairs (scored)")
    print(f"train base rate: {base:.1f}% retained\n")

    system_prompt = f"""\
You are a quantitative analyst judging whether a measured correlation between
two equities will REPLICATE out of sample: whether its correlation in a later,
disjoint window stays at or above {lo:.1f}.

You see evidence from a discovery window only. You also have population
statistics measured on a SEPARATE set of pairs from the same band. Use them to
calibrate -- they are the base rates you would otherwise guess at.

Overall, {base:.0f}% of pairs in this band retained.

HOW STEADY THE COUPLING IS. The discovery window is cut into four consecutive
quarters and the correlation measured in each. The typical (median) quarter is
the single most informative thing you have:
{t_stab}

HOW CONCENTRATED IT IS. Share of total co-movement contributed by the ten
single largest days. A pair that only moves together on a handful of days is
different from one that moves together daily:
{t_conc}

HOW LIQUID THE THINNER LEG IS:
{t_liq}

Read these tables as rankings, not thresholds. A typical quarter well below
{lo:.1f} does NOT mean the pair fails -- most of the population sits there.

Weigh the evidence and classify `replication_expectation`: "high" if you expect
this pair to retain, "low" if not, "insufficient_data" only if the evidence
genuinely cannot support either. Never give a probability or a number.
"""

    briefs = {}
    for k in test:
        a, b = sym[li[k]], sym[lj[k]]
        quarters = "  ".join(f"Q{q + 1} {w[q, k]:.3f}" for q in range(WINDOWS))
        rel = []
        if (a, b) in supply:
            rel.append("a filed supply-chain relationship")
        if (a, b) in pmi:
            rel.append(f"co-mentioned in news (PMI {pmi[(a, b)]:+.2f})")
        briefs[f"p{k}"] = (
            f"discovery correlation: {d[band][k]:+.4f}\n"
            f"by quarter: {quarters}\n"
            f"typical quarter |corr|: {med_w[k]:.4f}\n"
            f"weakest quarter |corr|: {min_w[k]:.4f}\n"
            f"top-10-day share of co-movement: {top10[k] * 100:.1f}%\n"
            f"thinner leg log10 median $volume: {min_liq[k]:.1f}\n"
            f"known relationship: {'; '.join(rel) if rel else 'none on record'}\n"
        )

    client = build_analyst_client(load_settings(), mode="direct")
    if client is None:
        sys.exit("no API key configured")
    print(f"submitting {len(briefs)} requests…")
    t0 = time.time()
    notes = await client.classify(briefs, QuantAnalystNote, system_prompt=system_prompt)
    print(f"completed in {time.time() - t0:.0f}s")

    ok = [f"p{k}" for k in test if notes[f"p{k}"].status == "ok"]
    ki = np.array([int(k[1:]) for k in ok])
    y = vb[ki] >= lo
    order = {"low": 0, "insufficient_data": 1, "high": 2}
    m_s = np.array([order[notes[k].replication_expectation or "insufficient_data"]
                    for k in ok], dtype=float)

    a_haiku = auc(m_s, y)
    a_med = auc(med_w[ki], y)
    print(f"\n  scored {len(ok)}   test base rate {y.mean() * 100:.1f}% retained")
    print(f"  Haiku said 'high' for {(m_s == 2).mean() * 100:.0f}% of pairs")
    print(f"\n  AUC Haiku (enriched brief)   : {a_haiku:.4f}")
    print(f"  AUC deterministic median-qtr : {a_med:.4f}")
    print("  AUC Haiku (D-134, 3 numbers) : 0.6484")

    rng2 = np.random.default_rng(args.seed)
    gains, deficits = [], []
    for _ in range(2000):
        bs = rng2.integers(0, len(ok), len(ok))
        if y[bs].sum() in (0, len(bs)):
            continue
        gains.append(auc(m_s[bs], y[bs]) - 0.6484)
        deficits.append(auc(med_w[ki][bs], y[bs]) - auc(m_s[bs], y[bs]))
    g = np.percentile(gains, [2.5, 97.5])
    df = np.percentile(deficits, [2.5, 97.5])
    print(f"\n  gain over D-134 Haiku    95% CI [{g[0]:+.4f}, {g[1]:+.4f}]"
          f"  {'REAL' if g[0] > 0 else 'not established'}")
    print(f"  deficit vs deterministic 95% CI [{df[0]:+.4f}, {df[1]:+.4f}]"
          f"  {'still behind' if df[0] > 0 else 'not established'}")


if __name__ == "__main__":
    asyncio.run(main())
