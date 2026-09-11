"""Deterministic quant read of a candidate's correlation neighbourhood (PHASE-1).

Fisher CI width and the duplicate-series flag are never re-derived here --
both come from `lagmatrix.comovement`, the single place those calculations
live. This module adds one thing `comovement` doesn't: within-window
split-half sign stability, a cheap same-invocation check distinct from D-93's
years-long discovery/validation split.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from lagmatrix.comovement import confidence_interval, duplicate_flag
from lagmatrix.domain.models import Candidate, LagEdge, QuantPerspective


def compute_quant_perspective(
    candidate: Candidate,
    lag_edges: list[LagEdge],
    closes: pd.DataFrame,
    trail: int,
    excluded_etfs: frozenset[str],
) -> QuantPerspective:
    correlation_edges = [e for e in lag_edges if e.relation == "correlation"]
    n_edges = len(correlation_edges)

    ci_widths: list[float] = []
    dup_count = 0
    agree_count = 0

    if correlation_edges:
        returns = closes.pct_change()
        sessions = closes.index
        # D-16: the window ends strictly before the as-of session.
        ti = sessions.get_loc(pd.Timestamp(candidate.as_of, tz=sessions.tz))
        window = returns.iloc[ti - trail : ti]
        half = trail // 2
        first_half, second_half = window.iloc[:half], window.iloc[half:]

        for edge in correlation_edges:
            lo, hi = confidence_interval(edge.correlation, trail)
            ci_widths.append(hi - lo)

            if duplicate_flag(window[candidate.symbol], window[edge.leader]) is not None:
                dup_count += 1

            corr_first = first_half[candidate.symbol].corr(first_half[edge.leader])
            corr_second = second_half[candidate.symbol].corr(second_half[edge.leader])
            if np.sign(corr_first) == np.sign(corr_second):
                agree_count += 1

    return QuantPerspective(
        n_edges=n_edges,
        median_ci_width=float(np.median(ci_widths)) if ci_widths else None,
        duplicate_count=dup_count,
        split_half_sign_agree_pct=(agree_count / n_edges * 100.0) if n_edges else None,
        candidate_is_etf=candidate.symbol in excluded_etfs,
        note=(
            f"{n_edges} correlation edge(s) over a {trail}-session trailing window"
            if n_edges
            else "no correlation edges in this candidate's neighbourhood"
        ),
    )
