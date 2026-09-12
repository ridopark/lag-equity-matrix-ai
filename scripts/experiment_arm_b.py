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
from lagmatrix.comovement import confidence_interval  # noqa: E402
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


def brief(corr: float, ci_w: float, n_sess: int, agrees: bool, lo: float) -> str:
    return (
        f"discovery correlation: {corr:+.4f}\n"
        f"discovery band: {lo:.1f}-{lo + 0.1:.1f} (replication means validation "
        f"|corr| >= {lo:.1f})\n"
        f"95% Fisher CI width: {ci_w:.4f} (a LOWER BOUND -- computed from the "
        f"session count, which assumes every session was valid)\n"
        f"discovery sessions: {n_sess}\n"
        f"split-half sign agreement within the discovery window: "
        f"{'agrees' if agrees else 'DISAGREES'}\n"
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="pairs to sample (PAID)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change().iloc[1:]
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

    briefs, truth, rule = {}, {}, {}
    for lo, take in picks:
        for k in take:
            key = f"p{k}"
            ci_lo, ci_hi = confidence_interval(float(d[k]), len(zd))
            briefs[key] = brief(float(d[k]), ci_hi - ci_lo, len(zd), bool(agrees[k]), lo)
            truth[key] = bool(v[k] >= lo)
            rule[key] = bool(agrees[k])

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
    abst = [k for k in ok if notes[k].replication_expectation == "insufficient_data"]
    scored = [k for k in ok if k not in set(abst)]
    if errs:
        print(f"  errors: {len(errs)}  e.g. {notes[errs[0]].reasoning[:120]}")
    if not scored:
        sys.exit("no scoreable notes came back")

    model_acc = np.mean(
        [(notes[k].replication_expectation == "high") == truth[k] for k in scored]
    ) * 100
    rule_on_scored = np.mean([rule[k] == truth[k] for k in scored]) * 100
    gap = model_acc - rule_on_scored

    print(f"\n  scored:      {len(scored)}   abstained: {len(abst)}   errors: {len(errs)}")
    print(f"  rule  accuracy (same pairs): {rule_on_scored:.2f}%")
    print(f"  model accuracy             : {model_acc:.2f}%")
    print(f"  gap: {gap:+.2f}pp   (pre-committed margin {MARGIN_PP}pp)")
    print(f"\nVERDICT: {'MODEL ADDS SIGNAL' if gap > MARGIN_PP else 'NULL'}")


if __name__ == "__main__":
    asyncio.run(main())
