"""Shared fixtures. External systems are faked here — tests never hit real infra."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext


@pytest.fixture
def closes() -> pd.DataFrame:
    """120 sessions of synthetic closes.

    LEAD1/LEAD2 are a tightly correlated bloc; LEAD3 is independent; CAND tracks
    the bloc loosely. INDEP is noise. The bloc exists so the clustering discount
    has something to discount.

    LEAD4/LEAD5 are a second bloc, independent of the first, with CAND2 tracking
    it the same way CAND tracks LEAD1/LEAD2 — PHASE-2's fan-out tests need two
    candidates whose neighbourhoods do not overlap. Drawn after the original
    columns so the existing symbols' values are unchanged.

    CANDD is a synthetic -1x inverse of CAND (stands in for AAPD relative to
    AAPL) — a deterministic transform of CAND's own returns, not a new rng
    draw, so every existing column's values are unchanged.
    """
    rng = np.random.default_rng(0)
    n = 120
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    common = rng.normal(0, 0.01, n)
    r = {
        "LEAD1": common + rng.normal(0, 0.001, n),
        "LEAD2": common + rng.normal(0, 0.001, n),
        "LEAD3": rng.normal(0, 0.01, n),
        "CAND": common * 0.8 + rng.normal(0, 0.004, n),
        "INDEP": rng.normal(0, 0.01, n),
    }
    common2 = rng.normal(0, 0.01, n)
    r["LEAD4"] = common2 + rng.normal(0, 0.001, n)
    r["LEAD5"] = common2 + rng.normal(0, 0.001, n)
    r["CAND2"] = common2 * 0.8 + rng.normal(0, 0.004, n)
    r["CANDD"] = -1.0 * r["CAND"]
    return pd.DataFrame({k: 100 * np.exp(np.cumsum(v)) for k, v in r.items()}, index=idx)


@pytest.fixture
def run_graph(closes):
    def _run(candidates, *, signal_universe=frozenset(), with_news=False):
        g = build_graph(with_news=with_news)
        return g.invoke(
            {"candidates": candidates},
            context=LagMatrixContext(closes=closes,
                                      signal_universe=set(signal_universe)),
        )
    return _run
