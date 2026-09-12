"""Shared fixtures.

Most external systems are faked here. The exception is the ArangoDB-backed
suite (`test_arango_topology`, `test_vector_index`, `test_market_scan`), which
needs a real instance and seeds its own throwaway database — never the real
`lagmatrix`. The connection helpers for those live here rather than being
copy-pasted into three files, which is how they drifted out of sync with the
application in the first place (Q-43).

Those throwaway databases are per-process (Q-46). They used to be fixed names,
so two suites running at once — routine here, with several agents working the
same checkout — truncated each other's collections mid-test. Measured before
the fix: two concurrent runs produced `6 failed, 8 errors` and `3 failed`,
every failure in a live-ArangoDB file, while either run alone was green. A
shared name made the suite report failures that had nothing to do with the
code under test, which is worse than slow: it teaches you to distrust red.
"""

from __future__ import annotations

import asyncio
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


# Per-process, so concurrent runs cannot truncate each other (Q-46). The xdist
# worker id is preferred when present; the pid covers plain concurrent runs.
_RUN_ID = os.environ.get("PYTEST_XDIST_WORKER") or str(os.getpid())
_CREATED: set[str] = set()


def _unavailable(reason: str, system: str = "ArangoDB", where: str = ARANGO_URL):
    """Fail when a live instance was promised, skip when it was not."""
    if REQUIRE_LIVE:
        pytest.fail(f"LAGMATRIX_REQUIRE_LIVE=1 but {system} is unusable: {reason}")
    pytest.skip(f"{system} not usable at {where}: {reason}")


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
    if not db_name.startswith("test_"):
        raise ValueError(
            f"throwaway database names must start with 'test_', got {db_name!r} "
            "— this guard exists so a typo can never point the suite at `lagmatrix`")
    scoped = f"{db_name}_{_RUN_ID}"
    pw = arango_password()
    try:
        client = arango.ArangoClient(hosts=ARANGO_URL)
        sys_db = client.db("_system", username=ARANGO_USER, password=pw, verify=True)
        if not sys_db.has_database(scoped):
            sys_db.create_database(scoped)
        _CREATED.add(scoped)
        return client.db(scoped, username=ARANGO_USER, password=pw)
    except Exception as exc:
        _unavailable(str(exc))


def pytest_sessionfinish(session, exitstatus):
    """Drop this process's throwaway databases, so they do not accumulate.

    Only names this process actually created, and only ones carrying this run's
    id — `lagmatrix` can never be reached from here.
    """
    if not _CREATED:
        return
    import arango

    pw = arango_password()
    try:
        sys_db = arango.ArangoClient(hosts=ARANGO_URL).db(
            "_system", username=ARANGO_USER, password=pw, verify=True)
    except Exception:
        return
    for name in sorted(_CREATED):
        assert name.startswith("test_") and name.endswith(f"_{_RUN_ID}"), name
        try:
            sys_db.delete_database(name)
        except Exception:
            pass



# Q-53: postgres.copytrade:5432 is reachable from the lagmatrix namespace, so
# `lagmatrix.pg` connects directly rather than shelling out through kubectl.
# Defaults assume a local tunnel; override for a real target.
PG_HOST = os.environ.get("LAGMATRIX_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("LAGMATRIX_PG_PORT", "5432"))


def pg_conn_or_skip():
    """A live, read-only postgres connection for the one round-trip test.

    Same fail-vs-skip rule as `arango_db_or_skip`: skip when postgres genuinely
    is not reachable, fail under LAGMATRIX_REQUIRE_LIVE=1. `psycopg2` is not
    yet a project dependency, so its absence here also skips rather than fails
    -- the same convention `arango_db_or_skip` applies to `python-arango`.
    """
    pytest.importorskip("psycopg2")
    where = f"{PG_HOST}:{PG_PORT}"
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=1):
            pass
    except OSError as exc:
        _unavailable(str(exc), system="postgres", where=where)
    from lagmatrix import pg
    try:
        return pg.connect()
    except Exception as exc:
        _unavailable(str(exc), system="postgres", where=where)


def require_local_file(path: str, what: str) -> None:
    """Skip when a file that is deliberately not in the repo is absent, or fail
    when `LAGMATRIX_REQUIRE_LIVE=1` promised it would be there.

    Vendor bars and the embedding reference are gitignored -- this repo is
    public -- so CI genuinely cannot have them and a skip there is honest. What
    is not honest is skipping on a machine that was supposed to have them,
    which is how nine live tests stayed dead for weeks (Q-43). Same fail-vs-skip
    rule as `arango_db_or_skip`, so there is one convention rather than three.
    """
    if pathlib.Path(path).exists():
        return
    if REQUIRE_LIVE:
        pytest.fail(f"LAGMATRIX_REQUIRE_LIVE=1 but {path} is missing ({what})")
    pytest.skip(f"{path} not present on this machine ({what})")


@pytest.fixture(autouse=True)
def _isolate_allow_real():
    """Restore `serve.ALLOW_REAL` after every test.

    It is module-level global state, and several tests flip it to True to reach
    the real-data paths. None of them restored it, so whether a test that
    depends on the default (`serve.py:93`, `ALLOW_REAL = False`) passed came
    down to alphabetical file order -- `tests/test_serve_health.py` passed
    alone and failed in the full suite for exactly that reason. Autouse rather
    than a per-test monkeypatch so a test written next month cannot reintroduce
    it by forgetting.
    """
    import sys

    before = getattr(sys.modules.get("serve"), "ALLOW_REAL", False)
    yield
    mod = sys.modules.get("serve")
    if mod is not None:
        mod.ALLOW_REAL = before


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed with whatever encoder `NewsIndex` itself uses.

    Tests that seed a throwaway `article` collection must produce vectors in
    the same space the application queries with, and must not name the encoder
    library -- naming it means a library swap edits tests, which is how a test
    ends up asserting against the thing it was meant to be independent of.
    Handles both call conventions so it survives the sentence-transformers to
    fastembed swap unedited.
    """
    from lagmatrix.adapters.vector import NewsIndex

    model = NewsIndex(db=None)._model
    if hasattr(model, "encode"):
        return [list(v) for v in model.encode(texts, normalize_embeddings=True)]
    return [list(v) for v in model.embed(texts)]


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
def bars_ohlcv() -> pd.DataFrame:
    """Long-form synthetic daily bars for CAND, matching `data/bars.parquet`'s
    exact columns (symbol, timestamp, open, high, low, close, volume,
    trade_count, vwap, dollar_vol).

    10 ordinary trailing sessions, each with a plain, distinct volume /
    trade_count so a median over them is unambiguous, followed by one more
    session -- a stand-in for "today's" bar, carrying deliberately extreme
    volume / trade_count. Callers whose `candidate.as_of` is that last
    session's date get a fixture where an implementation that (incorrectly)
    folds the as_of session into its trailing liquidity read produces a
    visibly different median than one that correctly excludes it (D-16).
    """
    sessions = pd.bdate_range("2026-01-01", periods=11, tz="UTC")
    trailing, as_of_session = sessions[:10], sessions[10]
    volume = np.arange(1, 11) * 1_000
    trade_count = np.arange(1, 11) * 100
    close = np.full(10, 50.0)
    ordinary = pd.DataFrame({
        "symbol": "CAND",
        "timestamp": trailing,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "trade_count": trade_count,
        "vwap": close,
        "dollar_vol": close * volume,
    })
    extreme = pd.DataFrame({
        "symbol": ["CAND"],
        "timestamp": [as_of_session],
        "open": [50.0],
        "high": [50.0],
        "low": [50.0],
        "close": [50.0],
        "volume": [10_000_000],
        "trade_count": [1_000_000],
        "vwap": [50.0],
        "dollar_vol": [50.0 * 10_000_000],
    })
    return pd.concat([ordinary, extreme], ignore_index=True)


def invoke_graph(graph, *args, **kwargs):
    """Drive a compiled graph the way production does.

    Every production caller is async -- `runner.py:132` awaits `ainvoke`,
    `serve.py` and `capture_showcase.py` use `astream`, `daily_ingest.py`
    wraps `ainvoke` in `asyncio.run`. Sync `.invoke()` cannot run this graph
    at all once any node is `async def`: langgraph raises `TypeError: No
    synchronous function provided`. `capture_trace.py`'s docstring already
    recorded this when PHASE-5 made `retrieve_news` async; the tests below
    were simply never converted, because `retrieve_news` is gated behind
    `with_news=True` and they build with it off.

    The two analyst nodes are not gated, so the conversion is now forced --
    and wanted: the sync and async executors schedule differently (only the
    async one sets `__cancel_on_exit__`), so a test on the sync path was
    pinning behaviour production never exercises.
    """
    return asyncio.run(graph.ainvoke(*args, **kwargs))


@pytest.fixture
def run_graph(closes):
    def _run(candidates, *, signal_universe=frozenset(), with_news=False, llm=None, bars=None):
        g = build_graph(with_news=with_news)
        return asyncio.run(g.ainvoke(
            {"candidates": candidates},
            context=LagMatrixContext(closes=closes,
                                      signal_universe=set(signal_universe),
                                      llm=llm,
                                      bars=bars),
        ))
    return _run


class FakeAnalystClient:
    """Stand-in for `AnalystClient` (adapters/llm.py's protocol), shared by the
    quant and day-trade gather-node tests (PHASE-4/5) -- neither ever reaches
    the network. Records every `classify()` call (`.calls`) and returns
    `schema(**responses[key])` per key in `briefs`; a key missing from
    `responses` falls back to `schema(**default)`, so a test only has to
    spell out the fields that differ per candidate.
    """

    def __init__(self, responses: dict[str, dict] | None = None, default: dict | None = None):
        self.calls: list[dict] = []
        self._responses = responses or {}
        self._default = default or {}

    async def classify(self, briefs, schema, *, system_prompt):
        self.calls.append(
            {"briefs": dict(briefs), "schema": schema, "system_prompt": system_prompt}
        )
        return {
            key: schema(**self._responses.get(key, self._default)) for key in briefs
        }


@pytest.fixture
def fake_analyst_client():
    """Factory fixture: hands back the `FakeAnalystClient` class itself so
    each test constructs one with the canned `responses`/`default` it needs
    for whichever note schema (`QuantAnalystNote`/`DayTradeAnalystNote`) it
    is testing against."""
    return FakeAnalystClient
