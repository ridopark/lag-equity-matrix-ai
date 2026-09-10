"""RED: `/graph` and `/movers` must 400 on bad input, not 200 with an error body.

Q-47 (`docs/spikes/overall.md`): D-102 gave `/followers` and `/network` real
400s via `parse_bounded`, and D-104 did the same for `/run`'s `limit`. But
`/graph` and `/movers` still wrap their work in `except Exception as e:
self._json({"error": ...})`, and `_json` (`scripts/serve.py:634`) hardcodes
`self.send_response(200)`. So D-107's new `ValueError` on a malformed `as_of`
(`neighbourhood()` now does `date.fromisoformat(as_of)` before querying, and
`movers()` already did) surfaces as a 200 carrying an error body -- a client
that reads only the status code cannot tell "your input was bad" from "this
ran cleanly." That is the same sibling-endpoint inconsistency D-104 closed,
one layer up.

The rule this file pins -- stated once here rather than re-derived per test:

    An error detectable before the response begins is a 400. `/graph` and
    `/movers` build their whole JSON body in memory before calling
    `self.send_response`, so any exception raised while computing that body
    can still choose its status code; a `ValueError` from bad input must
    choose 400, not fall into the generic `_json({"error": ...})` 200 path.

    `/run` cannot join this rule for anything discovered after streaming
    starts: `_run` (`scripts/serve.py:642`) calls `self.send_response(200)`
    and `self.end_headers()` *before* `stream()` runs, to open the
    `text/event-stream` connection the browser is already reading. Once
    those headers are on the wire, the status line cannot change -- an error
    discovered mid-stream (anything inside `stream()` itself) is structurally
    stuck in the SSE body as an `event: error` frame, not a status code.
    `/run`'s `limit` already 400s (D-104) precisely because `parse_bounded`
    runs *before* `send_response` -- that is this same rule already applied,
    not an exception to it. This file does not re-test `/run`; D-104 pins
    that half.

Seam: an actual HTTP round-trip against `serve.Handler`, not the underlying
`movers()`/`neighbourhood()` functions -- those already have their own
coverage (`neighbourhood`'s `as_of` guard in
`tests/test_serve_asof_neighbourhood.py`; `parse_bounded`'s bounds in
`tests/test_serve_params.py`). What is *not* covered anywhere else is
`do_GET`'s mapping from "the function raised" to "the status code a client
sees," which only exists at the level of a real request/response -- there is
no smaller unit than a live socket that exercises `BaseHTTPRequestHandler`'s
`send_response`/`send_error` machinery. `ThreadingHTTPServer(("127.0.0.1", 0),
serve.Handler)` on an ephemeral port is `serve.main()`'s own construction
(`scripts/serve.py:685`) minus the `argparse`/`serve_forever()` blocking loop,
so this does not duplicate a second way of wiring the handler.

`movers()` and `neighbourhood()` are monkeypatched with narrow stand-ins that
share the one contract this file actually tests -- "raise ValueError for a
malformed `as_of`, else return a dict" -- rather than calling the real
functions. The real `movers()` reads `data/bars.parquet`, which a live
`daily_ingest.py` run may be writing to concurrently right now, and the real
`neighbourhood()` reaches `arango_db()`, which (unpatched) authenticates
against the actual `lagmatrix` database this project never lets a test
touch, even read-only. Both real functions' own `as_of` parsing is already
covered by the two files named above; stubbing them here keeps this file
about the one thing it is not already about: the HTTP status code.
"""

from __future__ import annotations

import http.client
import json
import pathlib
import sys
import threading
from datetime import date
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

MALFORMED_AS_OF = ["2019-1-1", "Jan 1 2019", "zzzz"]


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


@pytest.fixture
def live_server():
    """A real `serve.Handler` listening on an ephemeral port, per the module
    docstring -- `serve.main()`'s own wiring minus argv parsing and the
    blocking `serve_forever()` loop."""
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


def _get(port: int, path: str, **params) -> tuple[int, bytes]:
    query = urlencode(params)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", f"{path}?{query}" if query else path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _fake_neighbourhood(symbol, as_of):
    """Shares `neighbourhood`'s real D-107 contract (raise on bad `as_of`,
    else return a dict) without touching `arango_db()`/`lagmatrix`."""
    date.fromisoformat(as_of)
    return {"symbol": symbol, "as_of": as_of, "edges": [], "twohop": []}


def _fake_movers(as_of, top_n=25):
    """Shares `movers`'s real `as_of` contract without reading
    `data/bars.parquet`, which a live ingest run may be writing to."""
    date.fromisoformat(as_of)
    return {"as_of": as_of, "swept": 0, "movers_found": 0,
            "customers_covered": 0, "movers": []}


# ---------------------------------------------------------------------------
# The defect: a malformed `as_of` must 400, not 200-with-an-error-body.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_as_of", MALFORMED_AS_OF)
def test_graph_with_malformed_as_of_returns_400_not_200(bad_as_of, live_server, monkeypatch):
    """Falsifies if the status is 200 (today's behaviour: the `ValueError`
    from `date.fromisoformat` is caught by `do_GET`'s broad `except
    Exception` and answered via `_json`, which hardcodes 200)."""
    serve, port = live_server
    monkeypatch.setattr(serve, "neighbourhood", _fake_neighbourhood)

    status, _ = _get(port, "/graph", symbol="AAPL", as_of=bad_as_of)

    assert status == 400


@pytest.mark.parametrize("bad_as_of", MALFORMED_AS_OF)
def test_movers_with_malformed_as_of_returns_400_not_200(bad_as_of, live_server, monkeypatch):
    """Falsifies if the status is 200, for the same reason as above --
    `/movers`'s handler (`scripts/serve.py:570-577`) has the identical
    `except Exception: self._json({"error": ...})` shape."""
    serve, port = live_server
    monkeypatch.setattr(serve, "movers", _fake_movers)

    status, _ = _get(port, "/movers", as_of=bad_as_of)

    assert status == 400


# ---------------------------------------------------------------------------
# Guard: a well-formed request must still 200 with the real body -- proves a
# fix that 400s unconditionally (or stops forwarding the JSON) is also wrong.
# ---------------------------------------------------------------------------

def test_graph_with_well_formed_as_of_still_returns_200_with_body(live_server, monkeypatch):
    """Falsifies if the status is not 200, or the body is not exactly the
    dict `neighbourhood` returned -- pins that a fix for the 400 case must
    not swallow or alter a clean response."""
    serve, port = live_server
    monkeypatch.setattr(serve, "neighbourhood", _fake_neighbourhood)

    status, body = _get(port, "/graph", symbol="AAPL", as_of="2019-01-01")

    assert status == 200
    assert json.loads(body) == {
        "symbol": "AAPL", "as_of": "2019-01-01", "edges": [], "twohop": [],
    }


def test_movers_with_well_formed_as_of_still_returns_200_with_body(live_server, monkeypatch):
    """Falsifies if the status is not 200, or the body is not exactly the
    dict `movers` returned."""
    serve, port = live_server
    monkeypatch.setattr(serve, "movers", _fake_movers)

    status, body = _get(port, "/movers", as_of="2019-01-01")

    assert status == 200
    assert json.loads(body) == {
        "as_of": "2019-01-01", "swept": 0, "movers_found": 0,
        "customers_covered": 0, "movers": [],
    }


# ---------------------------------------------------------------------------
# Regression pin: /graph's existing non-alnum-symbol 400 must survive this
# change untouched -- it already works today via a check before `neighbourhood`
# is ever called, so this test is expected to pass now and stay passing.
# ---------------------------------------------------------------------------

def test_graph_with_non_alnum_symbol_still_returns_400(live_server):
    """Falsifies if the status is not 400 for a symbol `.isalnum()` rejects --
    guards against a fix that only special-cases `as_of` and accidentally
    loosens the pre-existing symbol check alongside it."""
    _, port = live_server

    status, _ = _get(port, "/graph", symbol="AB;DROP", as_of="2019-01-01")

    assert status == 400
