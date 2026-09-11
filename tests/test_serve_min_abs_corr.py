"""RED: `/followers` must honour a `min_abs_corr` query parameter, and the
default (function signature and handler fallback alike) must be 0.4, not 0.5.

Measured against the live pod just now: `/followers?symbol=LULU&as_of=...`
returns `min_abs_corr=0.5` in the response body no matter what value is
passed in the query string -- `0.4`, `0.3`, `0.9`, or omitted entirely all
come back identical. `do_GET`'s `/followers` branch (`scripts/serve.py`)
parses `symbol`, `as_of` and `top_n` from the query string but never reads
`min_abs_corr`, so `followers()` always runs at its own signature default.
`/network`'s handler, right below it, already does this correctly --

    min_abs_corr = parse_bounded((q.get("min_abs_corr") or ["0.5"])[0],
                                  float, 0.1, 1.0)

-- so `/followers` is the odd one out, not a case needing a new approach.
Separately, the owner has asked for the default itself to move from 0.5 to
0.4, for both `followers()` and `network()`.

Seam: `followers()`/`network()` are called directly for the filtering and
signature-default tests (1, 2, 6); the handler tests (3, 4, 5) go through a
real HTTP round-trip against `serve.Handler` on an ephemeral port, per
`tests/test_serve_error_status.py`'s `live_server` fixture -- calling
`followers(min_abs_corr=...)` directly would not touch the actual defect,
which lives in `do_GET`'s query-string parsing.

`COMOVE_CLOSES` is monkeypatched to a synthetic frame (never `data/bars.
parquet` or `data/bars-10y.parquet`); no ArangoDB, no postgres. The frame
engineers one pair, ZZFAKEA/ZZFAKEB, whose measured same-window correlation
(confirmed directly against `lagmatrix.comovement.comovement_edges`) is
~0.467 -- above 0.4, below 0.5. That straddle is the whole point: a fixture
correlated at, say, 0.9 or 0.1 would pass against a handler that ignores
`min_abs_corr` entirely, since either threshold gives the same answer.
"""

from __future__ import annotations

import http.client
import inspect
import json
import pathlib
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

# n=260 business-day sessions ending 2026-01-30, seed=1, rho=0.45: measured
# directly against `comovement_edges(closes, as_of, trail=250, ...)` to give
# corr=0.467 for the ZZFAKEA/ZZFAKEB pair -- one edge at min_abs_corr=0.4,
# zero edges at min_abs_corr=0.5.
_SEED = 1
_RHO = 0.45
_N_SESSIONS = 260


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


def _straddling_closes() -> pd.DataFrame:
    rng = np.random.default_rng(_SEED)
    a = rng.normal(0, 0.01, _N_SESSIONS)
    b = _RHO * a + rng.normal(0, 0.01 * np.sqrt(1 - _RHO**2), _N_SESSIONS)
    idx = pd.bdate_range(end="2026-01-30", periods=_N_SESSIONS, tz="UTC")
    return pd.DataFrame(
        {"ZZFAKEA": 100 * np.cumprod(1 + a), "ZZFAKEB": 100 * np.cumprod(1 + b)},
        index=idx,
    )


@pytest.fixture
def live_server():
    """A real `serve.Handler` listening on an ephemeral port -- `serve.main()`'s
    own wiring minus argv parsing and the blocking `serve_forever()` loop.
    Mirrors `tests/test_serve_error_status.py`'s fixture of the same name.
    """
    import serve

    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    port = srv.server_address[1]
    try:
        yield serve, port
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _get(port: int, path: str, **params) -> tuple[int, dict]:
    query = urlencode(params)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", f"{path}?{query}" if query else path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, (json.loads(body) if resp.status == 200 else {})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. `followers()` filters by a supplied `min_abs_corr` and echoes it back.
# ---------------------------------------------------------------------------

def test_followers_includes_the_straddling_pair_at_0_4_but_excludes_it_at_0_5(monkeypatch):
    """The engineered pair sits at corr~0.467: `min_abs_corr=0.4` must
    include it, `min_abs_corr=0.5` must exclude it, and each call must echo
    back the threshold it was actually filtered at.

    Falsifies if: `followers_found` or `min_abs_corr` in either response
    fails to match the threshold actually passed in.
    """
    import serve

    closes = _straddling_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    as_of = closes.index[-1].date().isoformat()

    lenient = serve.followers("ZZFAKEA", as_of, min_abs_corr=0.4)
    strict = serve.followers("ZZFAKEA", as_of, min_abs_corr=0.5)

    assert lenient["min_abs_corr"] == 0.4
    assert lenient["followers_found"] == 1
    assert strict["min_abs_corr"] == 0.5
    assert strict["followers_found"] == 0


# ---------------------------------------------------------------------------
# 2 & 6. Signature defaults move from 0.5 to 0.4.
# ---------------------------------------------------------------------------

def test_followers_signature_default_min_abs_corr_is_0_4():
    """Falsifies if `followers`'s own `min_abs_corr` parameter default is
    anything other than `0.4` (today: `0.5`)."""
    import serve

    default = inspect.signature(serve.followers).parameters["min_abs_corr"].default
    assert default == 0.4


def test_network_signature_default_min_abs_corr_is_0_4():
    """Falsifies if `network`'s own `min_abs_corr` parameter default is
    anything other than `0.4` (today: `0.5`)."""
    import serve

    default = inspect.signature(serve.network).parameters["min_abs_corr"].default
    assert default == 0.4


# ---------------------------------------------------------------------------
# 3. The actual defect: the HTTP handler must pass a supplied `min_abs_corr`
#    through to `followers()`, not silently drop it.
# ---------------------------------------------------------------------------

def test_followers_handler_passes_a_supplied_min_abs_corr_through_to_followers(
        monkeypatch, live_server):
    """Calling `followers(min_abs_corr=...)` directly would pass today, since
    the defect is entirely in `do_GET`'s query-string parsing, not in
    `followers()` itself. This goes through a real request instead.

    `0.3` is deliberately neither the old default (0.5) nor the new one
    (0.4): if the handler ignores the query string and falls back to
    *either* default, the echoed `min_abs_corr` cannot coincidentally equal
    0.3, so this isolates "the handler reads the parameter" from "someone
    changed the default literal" (tests 2/4/6 pin the latter separately).

    Falsifies if: the `/followers?...&min_abs_corr=0.3` response's
    `min_abs_corr` != 0.3 (today: the handler never reads `min_abs_corr`
    from the query string, so it always runs at `followers()`'s own
    default -- currently 0.5 -- and echoes that instead).
    """
    serve, port = live_server
    closes = _straddling_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    as_of = closes.index[-1].date().isoformat()

    status, body = _get(port, "/followers", symbol="ZZFAKEA", as_of=as_of, min_abs_corr="0.3")

    assert status == 200
    assert body["min_abs_corr"] == 0.3
    assert body["followers_found"] == 1


# ---------------------------------------------------------------------------
# 4. The handler's own fallback default, when the parameter is absent, is 0.4.
# ---------------------------------------------------------------------------

def test_followers_handler_defaults_to_0_4_when_min_abs_corr_is_absent(monkeypatch, live_server):
    """No `min_abs_corr` in the query string at all -- pins the handler's own
    fallback literal (mirroring `/network`'s `(q.get("min_abs_corr") or
    ["0.5"])[0]`), which is a second place the 0.5->0.4 change must land
    and can drift independently of `followers()`'s own signature default.

    Falsifies if: the response's `min_abs_corr` != 0.4, or `followers_found`
    != 1 (today: the handler never reads the parameter and the function
    default is still 0.5, which excludes the straddling pair).
    """
    serve, port = live_server
    closes = _straddling_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    as_of = closes.index[-1].date().isoformat()

    status, body = _get(port, "/followers", symbol="ZZFAKEA", as_of=as_of)

    assert status == 200
    assert body["min_abs_corr"] == 0.4
    assert body["followers_found"] == 1


# ---------------------------------------------------------------------------
# 5. Out-of-range values 400, matching `/network`'s existing behaviour.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", ["5.0", "0.01"])
def test_followers_handler_rejects_out_of_range_min_abs_corr_with_400(
        bad_value, monkeypatch, live_server):
    """`/network` already rejects `min_abs_corr` outside [0.1, 1.0] via
    `parse_bounded`, producing a 400 (`tests/test_serve_params.py`,
    D-102/D-108). `/followers` must match exactly -- same bounds, same
    status.

    Falsifies if: the status is not 400 (today: `/followers` never parses
    `min_abs_corr` at all, so any value -- in range or not -- is silently
    accepted and answered 200).
    """
    serve, port = live_server
    closes = _straddling_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    as_of = closes.index[-1].date().isoformat()

    status, _ = _get(port, "/followers", symbol="ZZFAKEA", as_of=as_of, min_abs_corr=bad_value)

    assert status == 400
