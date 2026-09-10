"""RED (PHASE-1 of `docs/plans/PLAN-2026-09-09-homelab-deploy.md`): `/health`.

The Dockerfile's `HEALTHCHECK` and the k8s liveness/readiness probes both
target `GET /health`, and it does not exist yet -- `grep -n '"/health"'
scripts/serve.py` returns nothing today.

**Liveness, not readiness -- decided here, not assumed.** `/health` must
answer 200 the instant the process is up, independent of whether ArangoDB is
reachable or any data file is present. A probe that fails when a *dependency*
is down gets the pod killed for an outage it did not cause -- the opposite of
what a liveness probe is for. `/config` already reports dependency state
non-fatally (`"graphrag": arango_db() is not None`), so it -- not `/health`
-- is the readiness signal the k8s manifests should point a `readinessProbe`
at; this plan does not add a second, redundant readiness endpoint. `/health`
must therefore never call `arango_db()` and never read a parquet file --
`default_as_of()` now goes through `COMOVE_CLOSES()`, which is a ~650MiB read
(D-109), and a liveness probe hitting that every few seconds would be its own
outage. Every test below monkeypatches `arango_db`/`pd.read_parquet` to
*raise* rather than merely asserting they weren't slow, so a regression that
adds either call fails loudly instead of just being slow in CI.

Seam: a real HTTP round-trip against `serve.Handler`, matching the
`ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)` harness already
established in `tests/test_serve_error_status.py::live_server` (`serve.
main()`'s own construction minus argv parsing and the blocking
`serve_forever()` loop) -- reused here rather than re-invented, since a
dispatch-shadowing bug (`/health` never being reached, or falling through to
the `/` page handler or the 404) is only observable at the level of a real
request, not by calling `do_GET` off-socket.
"""

from __future__ import annotations

import http.client
import json
import pathlib
import sys
import threading

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


@pytest.fixture
def live_server():
    """A real `serve.Handler` listening on an ephemeral port -- see module
    docstring. Duplicated from `test_serve_error_status.py` rather than
    imported, matching that file's own note that a new file is fine but a
    second harness idiom is not: this is the same construction, not a
    different one."""
    from http.server import ThreadingHTTPServer

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


def _get(port: int, path: str) -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _forbid(name):
    def _raise(*a, **k):
        raise AssertionError(f"/health must not call {name}")
    return _raise


def test_health_returns_200_json_with_status_ok(live_server, monkeypatch):
    """Pins the response contract a Docker HEALTHCHECK/k8s probe reads:
    200, JSON, `{"status": "ok"}`.

    Falsifies today: there is no `/health` branch in `do_GET`, so this
    request falls through to `self.send_error(404)` and the assertion on
    `status == 200` fails. Would also falsify a fix that answers on `/health`
    but with a non-200 status, a non-JSON content type, or a body that omits
    `status`.
    """
    serve, port = live_server
    monkeypatch.setattr(serve, "arango_db", _forbid("arango_db"), raising=True)
    monkeypatch.setattr(serve, "COMOVE_CLOSES", _forbid("COMOVE_CLOSES"), raising=True)
    monkeypatch.setattr("pandas.read_parquet", _forbid("pandas.read_parquet"))

    status, headers, body = _get(port, "/health")

    assert status == 200
    assert headers.get("Content-Type") == "application/json"
    assert json.loads(body) == {"status": "ok"}


def test_health_does_not_require_allow_real(live_server):
    """A liveness probe must not depend on how the process was started.

    Falsifies if `/health` is gated behind `ALLOW_REAL` (e.g. reuses the
    `PermissionError("... needs real market data; start with --allow-real")`
    guard every real-data endpoint raises) -- `live_server` never sets
    `ALLOW_REAL`, so the module default (`False`) is in effect here, the same
    as a freshly started pod before any flag is considered.
    """
    serve, port = live_server
    assert serve.ALLOW_REAL is False

    status, _, _ = _get(port, "/health")

    assert status == 200


def test_health_is_not_shadowed_by_the_page_route(live_server):
    """`/` is matched by exact equality (`url.path == "/"`) in today's
    `do_GET`, so `/health` cannot literally collide with it -- but a future
    edit that turns the page route into a prefix check, or reorders branches
    so a catch-all runs first, would silently swallow `/health` into the
    HTML page response. Checking the content type (not just the status)
    catches that: the page handler answers 200 with `text/html`, `/health`
    must answer 200 with `application/json`.

    Falsifies if `/health` returns the page's `text/html` content type
    instead of JSON.
    """
    _, port = live_server

    status, headers, _ = _get(port, "/health")

    assert status == 200
    assert headers.get("Content-Type") == "application/json"
