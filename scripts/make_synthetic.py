"""Generate a synthetic universe that exercises every verdict branch.

The real baseline needs `data/bars.parquet` and `data/fires.csv`, which are not
committed — vendor prices and a private signal feed (D-51). So the guard could
not run from a clone. This builds a fabricated universe that can (D-52).

It guards *pipeline behaviour*, not market truth: nothing here is a real price
or a real signal. That is the point — the real data only produced whichever
branches the market happened to produce, whereas each candidate below is
constructed to land on a chosen one, so a refactor that breaks any single branch
shows up as a diff.

Construction. Each candidate is driven by two orthogonal factors and given two
sub-blocs of ten names that load on them, so the sub-blocs separate at the
`cluster_rho=0.7` threshold and the twenty of them survive `topk`. Independence
weighting then yields ~1/10 per name, ~2.0 effective — clear of `MIN_EFFECTIVE`
rather than sitting on it. Shocks are injected on the candidate's own session,
which is the last of the `move_win=3` window `leader_state` reads.

Usage:  uv run python scripts/make_synthetic.py
"""

from __future__ import annotations

import csv
import pathlib

import numpy as np
import pandas as pd

OUT = pathlib.Path("tests/fixtures")
SESSIONS = 100          # > trail=60, with room for an early no-history candidate
DAILY_VOL = 0.015
SHOCK = 0.10            # ~3.8 sigma over the 3-session window
SEED = 20260906

# (ticker, direction, session index, what the construction aims at)
PLAN = [
    ("SYNA", "up",   80, "corroborated: both blocs shock with the signal"),
    ("SYNB", "up",   80, "contradicted: both blocs shock against it"),
    ("SYNC", "up",   80, "neutral, low evidence: nothing crosses 2 sigma"),
    ("SYND", "up",   80, "neutral, tie: one bloc each way, equal weight"),
    ("SYNE", "down", 80, "corroborated on a down signal: exercises want=-1"),
    ("SYNF", "up",   30, "no_assessment: fewer than trail=60 prior sessions"),
]


def build() -> tuple[pd.DataFrame, list[dict]]:
    rng = np.random.default_rng(SEED)
    idx = pd.date_range("2026-01-05", periods=SESSIONS, freq="B", tz="UTC")
    cols: dict[str, np.ndarray] = {}

    for name, _direction, _si, _aim in PLAN:
        f1 = rng.normal(0, DAILY_VOL, SESSIONS)
        f2 = rng.normal(0, DAILY_VOL, SESSIONS)
        # candidate loads on both, so both blocs correlate with it and survive topk
        cols[name] = 0.6 * f1 + 0.6 * f2 + rng.normal(0, DAILY_VOL * 0.3, SESSIONS)
        for bloc, f in (("X", f1), ("Y", f2)):
            for j in range(10):
                cols[f"{name}{bloc}{j:02d}"] = f + rng.normal(0, DAILY_VOL * 0.25, SESSIONS)

    for j in range(40):  # unrelated names, so the pool is bigger than topk
        cols[f"BG{j:02d}"] = rng.normal(0, DAILY_VOL, SESSIONS)

    rets = pd.DataFrame(cols, index=idx)

    # Shocks land on the candidate's own session: leader_state reads
    # returns.iloc[ti-3:ti] where ti is the first session after as_of.
    for name, _direction, si, _aim in PLAN:
        xs = [f"{name}X{j:02d}" for j in range(10)]
        ys = [f"{name}Y{j:02d}" for j in range(10)]
        if name == "SYNA":
            rets.loc[idx[si], xs + ys] += SHOCK
        elif name == "SYNB":
            rets.loc[idx[si], xs + ys] -= SHOCK
        elif name == "SYND":
            rets.loc[idx[si], xs] += SHOCK
            rets.loc[idx[si], ys] -= SHOCK
        elif name == "SYNE":
            rets.loc[idx[si], xs + ys] -= SHOCK
        # SYNC: deliberately unshocked. SYNF never reaches fusion.
        rets.loc[idx[si], name] = 0.0   # candidate itself stays put, so |cand_z| < |z|

    closes = 100.0 * (1.0 + rets).cumprod()
    fires = [
        {"ticker": n, "posted_at": idx[si].strftime("%Y-%m-%d %H:%M:%S+00"), "direction": d}
        for n, d, si, _aim in PLAN
    ]
    return closes, fires


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    closes, fires = build()
    closes.to_parquet(OUT / "synthetic-closes.parquet", compression="zstd", compression_level=9)
    with (OUT / "synthetic-fires.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "posted_at", "direction"])
        w.writeheader()
        w.writerows(fires)
    size = (OUT / "synthetic-closes.parquet").stat().st_size
    print(f"{OUT}/synthetic-closes.parquet: {closes.shape[0]} sessions x "
          f"{closes.shape[1]} symbols ({size / 1024:.0f} KB)")
    print(f"{OUT}/synthetic-fires.csv: {len(fires)} candidates")
    print("next: uv run python scripts/capture_baseline.py --synthetic")


if __name__ == "__main__":
    main()
