"""PHASE-10 Pre-registration A: does discovery-window split-half sign
agreement predict out-of-sample replication?

Free: pure pandas/numpy over `data/bars-10y.parquet`, no API calls, no
database. Arm A gates arms B and C -- if the deterministic signal adds
nothing, asking whether an LLM beats it is a much weaker question.

Pre-registered decision rule, fixed BEFORE running (PLAN-2026-09-11, and
this file was written from it):

    Within each of D-95's |corr| bands, compare mean validation |corr|
    between pairs whose DISCOVERY-window split-half sign agrees and those
    whose disagrees. It adds signal only if, in the two best-powered bands
    (0.3-0.4 and 0.4-0.5), "agrees" beats "disagrees" by >= 0.03 absolute
    correlation -- D-93's own bar. Smaller, or reversed, is a null.

The target is deliberately NOT D-95's "sign holds" statistic, which is
96-99% true per band and would hand a near-free win to any classifier,
including a trivial always-True one.

Reuses `experiment_lag_matrix.py`'s split and standardise functions rather
than re-deriving them -- that file owns the D-93/D-100 boundary pin and its
four loud precondition checks.

    uv run python scripts/experiment_quant_perspective.py
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from experiment_lag_matrix import (  # noqa: E402
    COVERAGE,
    split_at_boundary,
    standardise,
)

from lagmatrix.comovement import _IMPLAUSIBLE_RETURN_CUTOFF  # noqa: E402

BANDS = [(0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01)]
POWERED = [(0.3, 0.4), (0.4, 0.5)]     # the two the decision rule reads
MARGIN = 0.03                          # D-93's bar, pre-committed


def corr0(z: np.ndarray) -> np.ndarray:
    """Contemporaneous (lag-0) correlation, D-95's quantity."""
    return (z.T @ z) / len(z)


def main() -> None:
    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change().iloc[1:]
    # D-100. Without this the corrupt returns are live -- 11 of them, 4 in
    # discovery and 7 in validation -- and `standardise` subtracts the
    # cross-sectional mean, so ONE bad return becomes a common shock across all
    # 1,573 symbols on that date. LINE's 2024-07-25 alone contributes +0.1094
    # to mean pairwise validation correlation, against a true mean of 0.1516.
    # The first version of this script omitted the mask and its bands were
    # ~83% artefact; see D-132's correction.
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
    print(f"discovery {len(disc_raw):,} sessions   validation {len(val_raw):,}")
    print(f"symbols passing {COVERAGE:.0%} coverage in both: {n:,}")

    # Discovery correlation, and the same over each half of the discovery
    # window -- the split-half check `compute_quant_perspective` performs at
    # candidate level, here at pair level (the plan states the distinction).
    half = len(zd) // 2
    cd = corr0(zd)
    c1 = corr0(zd[:half])
    c2 = corr0(zd[half:])
    cv = corr0(zv)

    iu, ju = np.triu_indices(n, k=1)
    d = cd[iu, ju]
    v = np.abs(cv[iu, ju])
    agrees = np.sign(c1[iu, ju]) == np.sign(c2[iu, ju])
    ad = np.abs(d)
    print(f"unordered pairs: {len(d):,} (raw triangle; D-95's published "
          f"1,236,372 is after a |corr|>=0.95 screen this script does not apply)\n")

    print(f"{'band':>12}{'pairs':>10}{'agree':>9}{'disagree':>10}"
          f"{'val|c| agree':>14}{'val|c| dis':>12}{'gap':>9}")
    verdicts = []
    for lo, hi in BANDS:
        m = (ad >= lo) & (ad < hi)
        if not m.any():
            continue
        va, vd_ = v[m & agrees], v[m & ~agrees]
        if len(va) == 0 or len(vd_) == 0:
            continue
        gap = va.mean() - vd_.mean()
        mark = "  <- powered" if (lo, hi) in POWERED else ""
        print(f"{f'{lo}-{hi}':>12}{m.sum():>10,}{len(va):>9,}{len(vd_):>10,}"
              f"{va.mean():>14.4f}{vd_.mean():>12.4f}{gap:>9.4f}{mark}")
        if (lo, hi) in POWERED:
            verdicts.append(((lo, hi), gap, len(va), len(vd_)))

    print(f"\nPre-registered rule: both powered bands need gap >= {MARGIN}")
    passed = 0
    for band, gap, na, nd in verdicts:
        ok = gap >= MARGIN
        passed += ok
        print(f"  band {band[0]}-{band[1]}: gap {gap:+.4f} "
              f"({'MEETS' if ok else 'below'} {MARGIN})   n={na:,}/{nd:,}")
    print(f"\nVERDICT: {'ADDS SIGNAL' if passed == len(verdicts) else 'NULL'} "
          f"({passed}/{len(verdicts)} powered bands met the pre-committed margin)")


if __name__ == "__main__":
    main()
