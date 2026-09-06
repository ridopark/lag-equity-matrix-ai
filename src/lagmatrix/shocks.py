"""Standardised move detection, shared by the pipeline and any future scanner.

Kept out of `graph/nodes/` deliberately: in corroboration mode this runs over a
candidate's neighbourhood, in scan mode it would run over the whole universe.
Same maths, different scope.
"""

from __future__ import annotations

import math

import pandas as pd


def standardised_moves(
    returns: pd.DataFrame, symbols: list[str], window: int, baseline: pd.DataFrame
) -> pd.Series:
    """Cumulative return over `returns` per symbol, in units of its own sigma.

    `baseline` supplies the trailing sample the sigma is estimated from, and must
    end strictly before `returns` begins — that separation is what keeps the
    computation point-in-time.
    """
    cum = returns[symbols].sum()
    sigma = baseline[symbols].std() * math.sqrt(window)
    return (cum / sigma).replace([float("inf"), float("-inf")], pd.NA).dropna()
