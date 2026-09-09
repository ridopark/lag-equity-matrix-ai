"""Shared fixtures.

Most external systems are faked here. The exception is the ArangoDB-backed
suite (`test_arango_topology`, `test_vector_index`, `test_market_scan`), which
needs a real instance and seeds its own throwaway database — never the real
`lagmatrix`. The connection helpers for those live here rather than being
copy-pasted into three files, which is how they drifted out of sync with the
application in the first place (Q-43).
"""

from __future__ import annotations

import os
import pathlib
import socket
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import pytest

from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext

# Default aligned with `scripts/serve.py:52`. These previously defaulted to
# :8529 while the application used :19999, so every live test skipped as
# "not reachable" and the whole graph layer went unverified while the suite
# reported a healthy pass count (Q-43).
ARANGO_URL = os.environ.get("LAGMATRIX_ARANGO_URL", "http://localhost:19999")
ARANGO_USER = os.environ.get("LAGMATRIX_ARANGO_USER", "root")
# Set to 1 in CI, or locally when a tunnel is expected, to turn "no database"
# from a silent skip into a failure. Without it a missing instance still skips,
# which is right on a laptop with no tunnel — but there was previously no mode
# in which its absence was an error, so nobody learned the tests were dead.
REQUIRE_LIVE = os.environ.get("LAGMATRIX_REQUIRE_LIVE") == "1"
_PW_FILE = pathlib.Path.home() / ".lagmatrix-arango-pw"


def arango_password() -> str:
    """Env var first, then the same file `serve.py` reads. Never logged."""
    pw = os.environ.get("LAGMATRIX_ARANGO_PASSWORD")
    if pw is not None:
        return pw
    try:
        return _PW_FILE.read_text().strip()
    except OSError:
        return ""


def _unavailable(reason: str):
    """Fail when a live instance was promised, skip when it was not."""
    if REQUIRE_LIVE:
        pytest.fail(f"LAGMATRIX_REQUIRE_LIVE=1 but ArangoDB is unusable: {reason}")
    pytest.skip(f"ArangoDB not usable at {ARANGO_URL}: {reason}")


def arango_db_or_skip(db_name: str):
    """A handle to a throwaway database, creating it if absent.

    Skips (or fails, under REQUIRE_LIVE) rather than passing vacuously. The
    one-second TCP probe comes first because python-arango retries internally
    and takes ~54s to give up on an unreachable host.
    """
    arango = pytest.importorskip("arango")
    parsed = urlparse(ARANGO_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 8529), timeout=1):
            pass
    except OSError as exc:
        _unavailable(str(exc))
    pw = arango_password()
    try:
        client = arango.ArangoClient(hosts=ARANGO_URL)
        sys_db = client.db("_system", username=ARANGO_USER, password=pw, verify=True)
        if not sys_db.has_database(db_name):
            sys_db.create_database(db_name)
        return client.db(db_name, username=ARANGO_USER, password=pw)
    except Exception as exc:
        _unavailable(str(exc))



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
