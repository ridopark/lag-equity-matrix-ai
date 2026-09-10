"""D-93 follow-up: does a two-hop chain carry anything a direct pair does not?

The user's hypothesis is leader -> follower -> ... -> follower. D-93 tested every
DIRECT pair and found nothing that replicates. This asks the strictly different
question a chain could still pose: given X->Y and Y->Z both looked strong in
discovery, does X->Z (at the summed lag) hold up out of sample any better than a
pair picked at random at that same lag?

Same windows, same market-excess construction, same out-of-sample discipline.
"""
from __future__ import annotations

import sys
from datetime import date

import numpy as np
import pandas as pd
from scipy import stats

COVERAGE, K1, K2 = 0.90, 1, 2          # chain X ->(1)-> Y ->(2)-> Z, so X->Z at lag 3
TOP_LINKS = 300                        # strongest links per leg, in discovery only


def standardise(block: pd.DataFrame):
    ok = block.notna().mean() >= COVERAGE
    b = block.loc[:, ok]
    b = b.sub(b.mean(axis=1), axis=0)
    z = (b - b.mean()) / b.std(ddof=0)
    return z.fillna(0.0).to_numpy(), list(b.columns)


def lagged(z, k):
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
    zd, cd_cols = standardise(disc_raw)
    zv, cv_cols = standardise(val_raw)
    common = [c for c in cd_cols if c in set(cv_cols)]
    di = {c: i for i, c in enumerate(cd_cols)}
    vi = {c: i for i, c in enumerate(cv_cols)}
    zd = zd[:, [di[c] for c in common]]
    zv = zv[:, [vi[c] for c in common]]
    print(f"symbols {len(common):,}   chain X -({K1})-> Y -({K2})-> Z, "
          f"implying X->Z at lag {K1+K2}")

    c1, c2 = lagged(zd, K1), lagged(zd, K2)
    np.fill_diagonal(c1, 0.0)
    np.fill_diagonal(c2, 0.0)

    def top_pairs(c, n):
        f = np.abs(c).ravel()
        idx = np.argpartition(f, -n)[-n:]
        return list(zip(*np.unravel_index(idx, c.shape), strict=True))

    leg1 = top_pairs(c1, TOP_LINKS)
    leg2_by_y: dict[int, list[int]] = {}
    for y, z_ in top_pairs(c2, TOP_LINKS):
        leg2_by_y.setdefault(y, []).append(z_)

    chains = [(x, y, z_) for x, y in leg1 for z_ in leg2_by_y.get(y, []) if z_ != x]
    print(f"chains formed from the {TOP_LINKS} strongest links per leg: {len(chains):,}")
    if not chains:
        print("no two-hop chains exist among the strongest links — nothing to test")
        return

    c3d, c3v = lagged(zd, K1 + K2), lagged(zv, K1 + K2)
    xs = np.array([c[0] for c in chains])
    zs = np.array([c[2] for c in chains])
    dd, vv = c3d[xs, zs], c3v[xs, zs]
    agree = int(np.sum(np.sign(dd) == np.sign(vv)))
    p = stats.binomtest(agree, len(chains), 0.5, alternative="greater").pvalue

    rng = np.random.default_rng(0)
    ri = rng.integers(0, len(common), size=(len(chains), 2))
    rd, rv = c3d[ri[:, 0], ri[:, 1]], c3v[ri[:, 0], ri[:, 1]]
    ragree = int(np.sum(np.sign(rd) == np.sign(rv)))

    print(f"\n{'':<22}{'mean |c| disc':>15}{'mean |c| val':>14}{'sign agree':>12}")
    print(f"{'chained X->Z':<22}{np.abs(dd).mean():>15.4f}{np.abs(vv).mean():>14.4f}"
          f"{agree/len(chains):>11.1%}")
    print(f"{'random pairs, same lag':<22}{np.abs(rd).mean():>15.4f}{np.abs(rv).mean():>14.4f}"
          f"{ragree/len(chains):>11.1%}")
    print(f"\nbinomial p (chained vs 50%): {p:.3e}")
    lift = agree / len(chains) - ragree / len(chains)
    print(f"lift over random pairs at the same lag: {lift:+.1%}")
    print("\n" + "=" * 66)
    verdict = ("CHAINS ADD SOMETHING" if (agree / len(chains) >= 0.60 and p < 0.01
                                          and lift > 0.05) else "NULL — chains add nothing")
    print(f"  VERDICT: {verdict}")
    print("=" * 66)


if __name__ == "__main__":
    main()
