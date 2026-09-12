"""PHASE-10 Pre-registrations B (and C): does the LLM beat the rule?

Ground truth per pair (`retained`): validation `|corr|` stays at or above
its own discovery band's lower edge. Deliberately NOT D-95's "sign holds",
which is 96-99% true per band and would hand a near-free win to any
classifier including a trivial always-True one.

  rule   predicts retained=True iff the discovery split-half sign agrees
  model  predicts via `replication_expectation`: high->True, low->False,
         insufficient_data->abstain (excluded from the accuracy denominator,
         reported separately)

Decision rule, pre-committed (PLAN-2026-09-11, D-132): the model adds signal
only if its accuracy beats the rule's, on the identical sample, by more than
**1.4pp** (2x the 0.71pp SE at n=5,000). Otherwise null.

    uv run python scripts/experiment_arm_b.py --n 5          # smoke, cents
    uv run python scripts/experiment_arm_b.py --n 5000       # the real arm

`--n` is explicit and small by default on purpose: this submits paid calls.
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
from lagmatrix.comovement import (  # noqa: E402
    _IMPLAUSIBLE_RETURN_CUTOFF,
    confidence_interval,
)
from lagmatrix.config import load_settings  # noqa: E402
from lagmatrix.domain.models import QuantAnalystNote  # noqa: E402

POWERED = [(0.3, 0.4), (0.4, 0.5)]
MARGIN_PP = 1.4

SYSTEM_PROMPT = """\
You are a quantitative analyst judging whether a measured correlation between
two equities will REPLICATE out of sample.

You are given, for one pair, evidence measured over a discovery window only:
the correlation, the width of its 95% Fisher confidence interval, the number
of sessions, and whether the correlation's sign held between the first and
second halves of that window.

Classify `replication_expectation`:
  "high"  -- you expect the correlation to hold at or above its discovery
             band's lower edge in a later, disjoint window
  "low"   -- you expect it to fall below that edge
  "insufficient_data" -- the evidence does not support either call

You are never asked for, and must never give, a probability or a number.
"""


def brief(corr: float, ci_w: float, n_sess: int, agrees: bool, lo: float,
          h1: float, h2: float) -> str:
    return (
        f"discovery correlation: {corr:+.4f}\n"
        f"discovery band: {lo:.1f}-{lo + 0.1:.1f} (replication means validation "
        f"|corr| >= {lo:.1f})\n"
        f"95% Fisher CI width: {ci_w:.4f} (a LOWER BOUND -- computed from the "
        f"session count, which assumes every session was valid)\n"
        f"discovery sessions: {n_sess}\n"
        f"split-half sign agreement within the discovery window: "
        f"{'agrees' if agrees else 'DISAGREES'}\n"
        # The magnitudes, not just the sign. Measured: min(|h1|,|h2|) carries
        # 2.5-3.5x the incremental signal of the sign bit after controlling for
        # the discovery correlation, and lifts the achievable ceiling from
        # +0.35pp to +1.78pp over the constant baseline. Withholding them is
        # what made the original version of this experiment unmeasurable.
        f"first-half |corr|: {abs(h1):.4f}\n"
        f"second-half |corr|: {abs(h2):.4f}\n"
        f"weaker half |corr|: {min(abs(h1), abs(h2)):.4f}\n"
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="pairs to sample (PAID)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change().iloc[1:]
    # D-100, the same omission that corrupted arm A's first run.
    rets = rets.where(rets.abs() <= _IMPLAUSIBLE_RETURN_CUTOFF)
    disc_raw, val_raw = split_at_boundary(rets)
    zd, cols_d = standardise(disc_raw)
    zv_full, cols_v = standardise(val_raw)
    common = [c for c in cols_d if c in set(cols_v)]
    di = {c: i for i, c in enumerate(cols_d)}
    vi = {c: i for i, c in enumerate(cols_v)}
    zd = zd[:, [di[c] for c in common]]
    zv = zv_full[:, [vi[c] for c in common]]
    n = len(common)

    half = len(zd) // 2
    cd = (zd.T @ zd) / len(zd)
    c1 = (zd[:half].T @ zd[:half]) / half
    c2 = (zd[half:].T @ zd[half:]) / (len(zd) - half)
    cv = (zv.T @ zv) / len(zv)

    iu, ju = np.triu_indices(n, k=1)
    d, v = cd[iu, ju], np.abs(cv[iu, ju])
    agrees = np.sign(c1[iu, ju]) == np.sign(c2[iu, ju])
    ad = np.abs(d)

    rng = np.random.default_rng(args.seed)
    per_band = max(1, args.n // len(POWERED))
    picks = []
    for lo, hi in POWERED:
        idx = np.flatnonzero((ad >= lo) & (ad < hi))
        take = rng.choice(idx, size=min(per_band, len(idx)), replace=False)
        picks.append((lo, take))

    h1 = np.abs(c1[iu, ju])
    h2 = np.abs(c2[iu, ju])
    briefs, truth, rule, band_of = {}, {}, {}, {}
    for lo, take in picks:
        for k in take:
            key = f"p{k}"
            ci_lo, ci_hi = confidence_interval(float(d[k]), len(zd))
            briefs[key] = brief(float(d[k]), ci_hi - ci_lo, len(zd), bool(agrees[k]),
                                lo, float(h1[k]), float(h2[k]))
            truth[key] = bool(v[k] >= lo)
            rule[key] = bool(agrees[k])
            band_of[key] = lo

    print(f"sampled {len(briefs)} pairs across {len(POWERED)} bands "
          f"(seed {args.seed})")
    rule_acc = np.mean([rule[k] == truth[k] for k in briefs]) * 100
    print(f"rule accuracy on this sample: {rule_acc:.2f}%")

    settings = load_settings()
    client = build_analyst_client(settings, mode="batch")
    if client is None:
        sys.exit("no API key configured -- set LAGMATRIX_ANTHROPIC_API_KEY")
    print(f"model: {settings.model}\nsubmitting {len(briefs)} batch requests…")

    t0 = time.time()
    notes = await client.classify(briefs, QuantAnalystNote, system_prompt=SYSTEM_PROMPT)
    elapsed = time.time() - t0
    print(f"batch completed in {elapsed:.1f}s")

    ok = [k for k in briefs if notes[k].status == "ok"]
    errs = [k for k in briefs if notes[k].status == "error"]
    if errs:
        print(f"  errors: {len(errs)}  e.g. {notes[errs[0]].reasoning[:120]}")
    if not ok:
        sys.exit("no scoreable notes came back")

    # AUC, not accuracy. Measured: with a ~35% base rate, accuracy-minus-constant
    # swings 60pp across retention thresholds and is monotone in the base rate --
    # it measures where the threshold sits, not what the predictor knows. AUC is
    # flat at 0.52-0.56 across the same sweep. A 3-level ordinal recovers all but
    # ~0.012 AUC of a continuous oracle, so the closed Literal costs nothing.
    order = {"low": 0, "insufficient_data": 1, "high": 2}

    def auc(score: np.ndarray, y: np.ndarray) -> float:
        o = np.argsort(score, kind="mergesort")
        y = y[o]
        r = np.arange(1, len(y) + 1)
        p, q = y.sum(), (~y).sum()
        return float("nan") if p == 0 or q == 0 else (r[y].sum() - p * (p + 1) / 2) / (p * q)

    rng2 = np.random.default_rng(args.seed)
    print(f"\n{'band':>10}{'n':>7}{'abst':>7}{'AUC rule':>10}{'AUC model':>11}"
          f"{'gap':>9}{'2SE':>9}{'verdict':>10}")
    for lo, _ in picks:
        ks = [k for k in ok if band_of[k] == lo]
        if not ks:
            continue
        y = np.array([truth[k] for k in ks])
        r_s = np.array([1.0 if rule[k] else 0.0 for k in ks])
        m_s = np.array([order[notes[k].replication_expectation or "insufficient_data"]
                        for k in ks], dtype=float)
        n_abst = int((m_s == 1).sum())
        a_r, a_m = auc(r_s, y), auc(m_s, y)
        boot = []
        for _ in range(1000):
            idx = rng2.integers(0, len(ks), len(ks))
            if y[idx].sum() in (0, len(idx)):
                continue
            boot.append(auc(m_s[idx], y[idx]) - auc(r_s[idx], y[idx]))
        two_se = 2 * float(np.std(boot)) if boot else float("nan")
        gap = a_m - a_r
        verdict = "ADDS" if gap > two_se else "null"
        print(f"{f'{lo}-{lo + 0.1:.1f}':>10}{len(ks):>7}{n_abst:>7}{a_r:>10.4f}"
              f"{a_m:>11.4f}{gap:>+9.4f}{two_se:>9.4f}{verdict:>10}")

    print("\nDecision: the model adds signal in a band only if its AUC gap over "
          "the rule exceeds 2 bootstrapped SE on that band's own sample.")


if __name__ == "__main__":
    asyncio.run(main())
