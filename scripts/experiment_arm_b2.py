"""Arm B2: does Haiku calibrate when given the population context?

D-133 found Haiku ranks worse than sorting by `min(|h1|,|h2|)`. The failure
looked informational rather than a reasoning failure: it saw one pair at a
time with no base rate, and inferred "weaker half 0.19 < 0.4 threshold, so it
won't clear 0.4" -- locally sound, empirically false, and uncorrectable from
a single observation.

This gives it what I had: the base rate and the P(retained) curve by
weaker-half decile.

**Leakage guard, which decides whether this means anything.** That curve is
built from validation outcomes. It is therefore fitted on a TRAIN split of
pairs and Haiku is scored on a DISJOINT TEST split -- the same cross-fitting
the oracle used. Handing it a curve fitted on the pairs it is judging would
leak the answer and the result would be meaningless.

    uv run python scripts/experiment_arm_b2.py --n 400
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from experiment_lag_matrix import split_at_boundary, standardise  # noqa: E402

from lagmatrix.adapters.llm import build_analyst_client  # noqa: E402
from lagmatrix.comovement import _IMPLAUSIBLE_RETURN_CUTOFF  # noqa: E402
from lagmatrix.config import load_settings  # noqa: E402
from lagmatrix.domain.models import QuantAnalystNote  # noqa: E402

BAND = (0.4, 0.5)


def auc(score: np.ndarray, y: np.ndarray) -> float:
    o = np.argsort(score, kind="mergesort")
    y = y[o]
    r = np.arange(1, len(y) + 1)
    p, q = y.sum(), (~y).sum()
    return float("nan") if p == 0 or q == 0 else (r[y].sum() - p * (p + 1) / 2) / (p * q)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400, help="held-out pairs to score (PAID)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=3600.0)
    args = ap.parse_args()

    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
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
    n = len(common)
    h = len(zd) // 2
    cd = (zd.T @ zd) / len(zd)
    c1 = (zd[:h].T @ zd[:h]) / h
    c2 = (zd[h:].T @ zd[h:]) / (len(zd) - h)
    cv = (zv.T @ zv) / len(zv)
    iu, ju = np.triu_indices(n, k=1)
    d, v = cd[iu, ju], np.abs(cv[iu, ju])
    mn = np.minimum(np.abs(c1[iu, ju]), np.abs(c2[iu, ju]))
    ad = np.abs(d)

    lo, hi = BAND
    idx = np.flatnonzero((ad >= lo) & (ad < hi))
    rng = np.random.default_rng(args.seed)
    rng.shuffle(idx)
    cut = len(idx) // 2
    train, pool = idx[:cut], idx[cut:]                 # DISJOINT
    test = rng.choice(pool, size=min(args.n, len(pool)), replace=False)

    # The curve, fitted on TRAIN only.
    tr_y = v[train] >= lo
    tr_x = mn[train]
    edges = np.quantile(tr_x, np.linspace(0, 1, 11))
    rows = []
    for i in range(10):
        s = (tr_x >= edges[i]) & (tr_x < edges[i + 1] if i < 9 else tr_x <= edges[10])
        rows.append((edges[i], edges[i + 1], tr_y[s].mean() * 100, int(s.sum())))
    table = "\n".join(
        f"  weaker half {a:.3f}-{b:.3f}: {p:.0f}% of such pairs retained"
        for a, b, p, _ in rows
    )
    base = tr_y.mean() * 100
    print(f"train {len(train):,} pairs (curve fitted here)   test {len(test):,} pairs (scored)")
    print(f"train base rate: {base:.1f}% retained\n{table}\n")

    system_prompt = f"""\
You are a quantitative analyst judging whether a measured correlation between
two equities will REPLICATE out of sample: whether its correlation in a later,
disjoint window stays at or above {lo:.1f}.

You are given evidence from a discovery window only. You also have population
statistics, measured on a SEPARATE set of pairs from the same band -- use them
to calibrate. They are the base rates you would otherwise have to guess at:

  Overall, {base:.0f}% of pairs in this band retained.

  By the weaker of the two discovery half-window correlations:
{table}

Note what this implies: a weaker half well below {lo:.1f} does NOT mean the pair
fails. Pairs whose weaker half is around 0.30 still retained roughly two-thirds
of the time. The weaker half is informative as a RANKING, not as a threshold.

Classify `replication_expectation`: "high" if you expect this pair to retain,
"low" if not, "insufficient_data" only if the evidence genuinely cannot support
either. Never give a probability or a number.
"""

    briefs = {}
    for k in test:
        briefs[f"p{k}"] = (
            f"discovery correlation: {d[k]:+.4f}\n"
            f"first-half |corr|: {abs(c1[iu, ju][k]):.4f}\n"
            f"second-half |corr|: {abs(c2[iu, ju][k]):.4f}\n"
            f"weaker half |corr|: {mn[k]:.4f}\n"
            f"discovery sessions: {len(zd)}\n"
        )

    # Direct, not batch. Measured: a 1,000-request batch completed in 251s
    # while 6-request batches timed out past 900s twice -- small batches sit in
    # the queue. Direct returns in seconds and the cost difference here is
    # under a dollar.
    client = build_analyst_client(load_settings(), mode="direct")
    if client is None:
        sys.exit("no API key configured")
    print(f"submitting {len(briefs)} requests…")
    t0 = time.time()
    notes = await client.classify(briefs, QuantAnalystNote, system_prompt=system_prompt)
    print(f"completed in {time.time() - t0:.0f}s")

    ok = [f"p{k}" for k in test if notes[f"p{k}"].status == "ok"]
    y = np.array([v[int(k[1:])] >= lo for k in ok])
    order = {"low": 0, "insufficient_data": 1, "high": 2}
    m_s = np.array([order[notes[k].replication_expectation or "insufficient_data"] for k in ok],
                   dtype=float)
    det = np.array([mn[int(k[1:])] for k in ok])
    said_high = float((m_s == 2).mean() * 100)

    # Paired bootstrap. The gaps here are small enough that reporting them
    # without an error bar would be the same overclaiming this experiment
    # exists to catch.
    rng2 = np.random.default_rng(args.seed)
    gains, deficits = [], []
    for _ in range(2000):
        b = rng2.integers(0, len(ok), len(ok))
        if y[b].sum() in (0, len(b)):
            continue
        gains.append(auc(m_s[b], y[b]) - 0.5588)
        deficits.append(auc(det[b], y[b]) - auc(m_s[b], y[b]))
    g_lo, g_hi = np.percentile(gains, [2.5, 97.5])
    d_lo, d_hi = np.percentile(deficits, [2.5, 97.5])

    print(f"\n  scored {len(ok)}   test base rate {y.mean() * 100:.1f}% retained")
    print(f"  Haiku said 'high' for {said_high:.0f}% of pairs "
          f"(D-133's uncalibrated run: ~1 in 6)")
    print(f"  AUC Haiku (calibrated)      : {auc(m_s, y):.4f}")
    print(f"  AUC deterministic min|half| : {auc(det, y):.4f}")
    print("  AUC Haiku (D-133, no context): 0.5588")
    print(f"\n  gain from context      : {auc(m_s, y) - 0.5588:+.4f}  "
          f"95% CI [{g_lo:+.4f}, {g_hi:+.4f}]")
    print(f"  still behind sorting by: {auc(det, y) - auc(m_s, y):+.4f}  "
          f"95% CI [{d_lo:+.4f}, {d_hi:+.4f}]")


if __name__ == "__main__":
    asyncio.run(main())
