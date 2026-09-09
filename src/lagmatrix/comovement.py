"""Pairwise contemporaneous co-movement, measured directly from price history (D-95).

This measures how reliably two names move together *on the same session* — never
what one does after the other. D-95's out-of-sample calibration, run on the same
discover/validate split as D-93/D-94 across 1,236,371 pairs:

    disc band        n        val mean   val sd   sign holds
    0.3-0.40   115,155           0.260    0.121        98.7%
    0.4-0.50    39,990           0.350    0.139        98.1%
    0.5-0.60    12,291           0.460    0.164        96.2%
    0.6-0.70     4,707           0.587    0.167        98.1%
    0.7-0.95     2,214           0.712    0.129        99.5%

A pair measured at 0.6-0.7 lands at 0.587 +/- 0.167 four years later with its
sign intact 98% of the time — mild, consistent shrinkage. That replication is
about the *relationship*, not a forecast: same-day co-movement replicates at
0.640, next-day prediction replicates at 0.028 (D-93, D-94, both null). Nothing
here may be read as one name anticipating the other.

Same-company artefacts (GOOGL/GOOG, Z/ZG, FOX/FOXA, NWS/NWSA) are genuine,
tradeable share classes and are kept. A true data artefact — one price series
stored twice under two symbols (NATL/LINE, identical on 69.5% of sessions) — is
flagged instead of dropped; the caller decides what to do with a flagged edge.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from lagmatrix.domain.models import ComovementEdge

_DUPLICATE_MATCH_THRESHOLD = 0.5


def comovement_edges(
    closes: pd.DataFrame,
    as_of,
    trail: int = 250,
    min_abs_corr: float = 0.3,
    exclude: frozenset[str] = frozenset(),
) -> list[ComovementEdge]:
    """One edge per unordered pair whose |correlation| clears `min_abs_corr`,
    measured over the `trail` sessions strictly before `as_of` (D-16: `as_of`'s
    own session is never in the window).
    """
    session_dates = closes.index.date
    matches = np.where(session_dates == as_of)[0]
    if len(matches) == 0:
        return []
    ti = int(matches[0])
    if ti < trail:
        return []

    cols = [c for c in closes.columns if c not in exclude]
    window = closes[cols].pct_change().iloc[ti - trail : ti]
    n = len(window)

    corr = window.corr()
    symbols = corr.columns.to_numpy()
    values = corr.to_numpy()

    edges: list[ComovementEdge] = []
    iu, ju = np.triu_indices(len(symbols), k=1)
    for i, j in zip(iu, ju, strict=True):
        c = values[i, j]
        if np.isnan(c) or abs(c) < min_abs_corr:
            continue
        a, b = symbols[i], symbols[j]
        ci_low, ci_high = confidence_interval(float(c), n)
        edges.append(
            ComovementEdge(
                a=str(a),
                b=str(b),
                corr=float(c),
                n_sessions=n,
                ci_low=ci_low,
                ci_high=ci_high,
                flag=duplicate_flag(window[a], window[b]),
            )
        )
    return edges


def confidence_interval(corr: float, n: int) -> tuple[float, float]:
    """95% Fisher z-transform interval on a correlation measured from `n` sessions.

    `corr` is clamped just inside [-1, 1]: `atanh` diverges at the boundary, and
    a duplicate-series edge (D-95's NATL/LINE) can measure exactly +/-1.0.
    """
    clamped = max(min(corr, 1.0 - 1e-15), -1.0 + 1e-15)
    z = math.atanh(clamped)
    se = 1.0 / math.sqrt(n - 3)
    return math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se)


def duplicate_flag(ret_a: pd.Series, ret_b: pd.Series) -> str | None:
    """`"duplicate_series"` if the two return series are exactly equal on most
    sessions (NATL/LINE: one price series stored twice), else `None`. Genuine
    share classes correlate highly but essentially never tie exactly.
    """
    match_rate = (ret_a.to_numpy() == ret_b.to_numpy()).mean()
    return "duplicate_series" if match_rate > _DUPLICATE_MATCH_THRESHOLD else None
