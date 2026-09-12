"""Run-scoped dependencies injected into nodes via `Runtime`."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from lagmatrix.adapters.llm import AnalystClient


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
    halt_on_contradicted: bool = False  # PHASE-6: False (default) never interrupts `review`
    arango_topology: object | None = None  # set in PHASE-4; None disables graph traversal
    max_lag_hops: int = 2
    vector_index: object | None = None  # set in PHASE-7; None disables news
    llm: AnalystClient | None = None  # set in PHASE-6; None disables analyst nodes
    max_llm_candidates: int | None = None  # set in PHASE-6; None disables the batch cap
