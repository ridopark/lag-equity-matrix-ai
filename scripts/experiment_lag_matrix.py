"""D-93: does ANY lagged pairwise structure survive out of sample?

Run exactly as pre-registered (docs/spikes/overall.md D-93, commit e103a91),
written before this script existed. Read-only; touches no database.
"""
from __future__ import annotations

import sys
from datetime import date

import numpy as np
import pandas as pd
from scipy import stats

LAGS = [1, 2, 3, 5, 10]
TOP_N = 1000
SIGN_THRESHOLD = 0.60      # D-93, declared in advance
CORR_THRESHOLD = 0.03
COVERAGE = 0.90


def standardise(block: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Market-excess, then z-scored per column. Columns with thin coverage drop."""
    ok = block.notna().mean() >= COVERAGE
    b = block.loc[:, ok]
    b = b.sub(b.mean(axis=1), axis=0)                 # market-excess, equal-weighted
    z = (b - b.mean()) / b.std(ddof=0)
    return z.fillna(0.0).to_numpy(), list(b.columns)


def lagged_corr(z: np.ndarray, k: int) -> np.ndarray:
    """C[i, j] = corr(series i at t, series j at t+k)."""
    a, b = z[:-k], z[k:]
    return (a.T @ b) / len(a)


# D-95's discovery/validation boundary, pinned as a date rather than a fraction.
#
# It used to be `cut = int(len(rets) * 0.60)` -- a fraction of however many
# sessions the file happens to hold. Since `long_bars` now appends nightly that
# boundary slid: a year of runs moves it about seven months, and re-running this
# then prints a number that looks comparable to D-95's 0.586 and is not. With
# `*.parquet` gitignored and `fetch_daily_bars.py` rewriting split-affected
# history in place, no prior state is recoverable either.
#
# 2022-09-06 is not a chosen date. It is the boundary the file yielded when
# 0.586 was published (2,515 sessions, int(2515*0.60)=1509, rets.index[1509]),
# confirmed to reproduce D-95/D-100 across five quantities: 11 masked returns,
# 1,573 symbols, 1,236,372 pairs, 6 same-company drops, corr 0.5860.
#
# Compared as a date, never an index, so it survives rows appearing before it.
# Compared by session *date*. The bars are stamped 04:00:00+00:00 (midnight ET),
# so an exact-timestamp pin would silently miss -- and comparing dates survives
# a change in that convention, which an exact stamp would not.
VALIDATION_START = date(2022, 9, 6)
EXPECTED_DISCOVERY_SESSIONS = 1509
EXPECTED_DISCOVERY_END = date(2022, 9, 2)


def split_at_boundary(rets):
    """Discovery/validation either side of `VALIDATION_START`, or exit loudly.

    The first two checks catch a boundary that has gone missing. The last two
    catch something worse and quieter: history rewritten *underneath* a date
    that still exists, which `fetch_daily_bars.py` does when it refetches a
    split-affected symbol in full. Without them this stays deterministic while
    silently measuring a different experiment.
    """
    sessions = rets.index.date
    if not (sessions.min() <= VALIDATION_START <= sessions.max()):
        sys.exit(f"{VALIDATION_START} is outside the data "
                 f"({sessions.min()}..{sessions.max()})")
    if VALIDATION_START not in set(sessions):
        sys.exit(f"{VALIDATION_START} is not a session in this file; "
                 "splitting at the next one is the silent shift this pin exists "
                 "to prevent")
    disc = rets[sessions < VALIDATION_START]
    val = rets[sessions >= VALIDATION_START]
    if len(disc) != EXPECTED_DISCOVERY_SESSIONS:
        sys.exit(f"discovery has {len(disc)} sessions, expected "
                 f"{EXPECTED_DISCOVERY_SESSIONS} -- history before the boundary "
                 "has changed")
    if disc.index[-1].date() != EXPECTED_DISCOVERY_END:
        sys.exit(f"discovery ends {disc.index[-1].date()}, expected "
                 f"{EXPECTED_DISCOVERY_END} -- history before the "
                 "boundary has changed")
    return disc, val


def main() -> None:
    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change().iloc[1:]
    disc_raw, val_raw = split_at_boundary(rets)
    print(f"sessions {len(rets):,}   discovery {len(disc_raw):,} "
          f"({disc_raw.index[0].date()}..{disc_raw.index[-1].date()})   "
          f"validation {len(val_raw):,} "
          f"({val_raw.index[0].date()}..{val_raw.index[-1].date()})")

    zd, cols_d = standardise(disc_raw)
    zv_full, cols_v = standardise(val_raw)
    common = [c for c in cols_d if c in set(cols_v)]
    di = {c: i for i, c in enumerate(cols_d)}
    vi = {c: i for i, c in enumerate(cols_v)}
    zd = zd[:, [di[c] for c in common]]
    zv = zv_full[:, [vi[c] for c in common]]
    n = len(common)
    print(f"symbols passing {COVERAGE:.0%} coverage in both windows: {n:,}"
          f"   -> {n*n - n:,} directed pairs per lag")

    rng = np.random.default_rng(0)
    perm = rng.permutation(len(zv))
    zv_shuf = zv[perm]                                 # destroys lead-lag, keeps distributions

    print(f"\n{'lag':>4}{'sel |c| disc':>14}{'val mean |c|':>14}"
          f"{'sign agree':>12}{'p':>10}{'control':>10}")
    results = []
    for k in LAGS:
        cd = lagged_corr(zd, k)
        np.fill_diagonal(cd, 0.0)
        flat = np.abs(cd).ravel()
        idx = np.argpartition(flat, -TOP_N)[-TOP_N:]
        i_arr, j_arr = np.unravel_index(idx, cd.shape)
        sel_d = cd[i_arr, j_arr]

        cv = lagged_corr(zv, k)
        sel_v = cv[i_arr, j_arr]
        agree = int(np.sum(np.sign(sel_d) == np.sign(sel_v)))
        p = stats.binomtest(agree, TOP_N, 0.5, alternative="greater").pvalue

        cs = lagged_corr(zv_shuf, k)
        agree_s = int(np.sum(np.sign(sel_d) == np.sign(cs[i_arr, j_arr])))

        results.append((k, np.abs(sel_d).mean(), np.abs(sel_v).mean(),
                        agree / TOP_N, p, agree_s / TOP_N, i_arr, j_arr, sel_d, sel_v))
        print(f"{k:>4}{np.abs(sel_d).mean():>14.4f}{np.abs(sel_v).mean():>14.4f}"
              f"{agree/TOP_N:>11.1%}{p:>10.2e}{agree_s/TOP_N:>10.1%}")

    print("\n" + "=" * 74)
    print(f"PRE-COMMITTED RULE: sign-agreement >= {SIGN_THRESHOLD:.0%} AND "
          f"val mean |corr| >= {CORR_THRESHOLD} AND p < 0.01")
    any_pass = False
    for k, _md, mv, sa, p, ctl, *_ in results:
        ok = sa >= SIGN_THRESHOLD and mv >= CORR_THRESHOLD and p < 0.01
        artefact = ctl >= sa - 0.02
        any_pass |= ok and not artefact
        print(f"  lag {k:>2}: sign {sa:.1%} {'OK ' if sa>=SIGN_THRESHOLD else 'no '}"
              f"| val|c| {mv:.4f} {'OK ' if mv>=CORR_THRESHOLD else 'no '}"
              f"| p {p:.1e} {'OK ' if p<0.01 else 'no '}"
              f"| control {ctl:.1%}{'  <- ARTEFACT' if artefact else ''}")
    print(f"\n  VERDICT: {'STRUCTURE CLAIMED' if any_pass else 'NULL at every lag'}")
    print("=" * 74)

    best = max(results, key=lambda r: r[3])
    k, _, _, sa, _, _, i_arr, j_arr, sel_d, sel_v = best
    print(f"\nstrongest lag ({k}), 10 selected pairs — discovery vs validation corr:")
    order = np.argsort(-np.abs(sel_d))[:10]
    for o in order:
        print(f"  {common[i_arr[o]]:>6} -> {common[j_arr[o]]:<6} "
              f"disc {sel_d[o]:+.3f}   val {sel_v[o]:+.3f}"
              f"   {'agree' if np.sign(sel_d[o])==np.sign(sel_v[o]) else 'FLIP'}")


if __name__ == "__main__":
    main()
