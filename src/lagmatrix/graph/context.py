"""Run-scoped dependencies injected into nodes via `Runtime`."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class LagMatrixContext:
    """Run-scoped dependencies. Passed via `invoke(..., context=...)`; nodes
    read it off `Runtime`, so nothing is hidden in a closure."""

    closes: pd.DataFrame
    signal_universe: set[str]
    excluded_symbols: frozenset[str] = frozenset()
    trail: int = 60
    topk: int = 20
    move_win: int = 3
    sigma: float = 2.0
    cluster_rho: float = 0.7
    news_lookback_days: int = 5
    news_limit: int = 20
    news_client: object | None = None  # set in PHASE-5; None disables news
    halt_on_contradicted: bool = False  # PHASE-6: False (default) never interrupts `review`
