"""`arango_db()` must honour LAGMATRIX_ARANGO_USER.

`serve.py` hardcodes `username="root"` while `tests/conftest.py` already reads
`LAGMATRIX_ARANGO_USER` -- the variable exists in the test helper and the
application ignores it. That is the same drift Q-43 was about, where conftest
and serve.py disagreed about the URL and nine live tests skipped silently for
weeks as a result.

It matters now because the web pod should connect as a read-only role
(`lagmatrix_ro`, verified to have `ro` on `lagmatrix` and `none` on `_system`)
rather than as root. Every ArangoDB call reachable from an HTTP request is an
`aql.execute` read; the only writer, `upsert_comovement`, has one caller, the
ingest CronJob. Without this the demotion cannot be expressed in a manifest.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


def test_arango_db_uses_the_configured_user(monkeypatch, tmp_path):
    """Falsifiable: fails if `arango_db()` passes a hardcoded "root" rather than
    the configured user."""
    import serve

    pw = tmp_path / "pw"
    pw.write_text("secret\n")
    seen = {}

    class _Client:
        def __init__(self, hosts): pass
        def db(self, name, username, password):
            seen.update(name=name, username=username, password=password)
            return "db-handle"

    monkeypatch.setattr(serve, "ARANGO_URL", "http://arangodb:8529")
    monkeypatch.setenv("LAGMATRIX_ARANGO_USER", "lagmatrix_ro")
    monkeypatch.setattr(serve.socket, "create_connection",
                        lambda *a, **k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(serve.os.path, "expanduser", lambda p: str(pw))
    monkeypatch.setitem(sys.modules, "arango",
                        type("m", (), {"ArangoClient": _Client}))

    serve.arango_db()
    assert seen.get("username") == "lagmatrix_ro", (
        f"connected as {seen.get('username')!r}; LAGMATRIX_ARANGO_USER was ignored")


def test_arango_db_defaults_to_root_when_unset(monkeypatch, tmp_path):
    """The default must not change: an operator with no env var set keeps
    today's behaviour. Falsifiable if the default becomes anything else."""
    import serve

    pw = tmp_path / "pw"
    pw.write_text("secret\n")
    seen = {}

    class _Client:
        def __init__(self, hosts): pass
        def db(self, name, username, password):
            seen.update(username=username)
            return "db-handle"

    monkeypatch.delenv("LAGMATRIX_ARANGO_USER", raising=False)
    monkeypatch.setattr(serve, "ARANGO_URL", "http://arangodb:8529")
    monkeypatch.setattr(serve.socket, "create_connection",
                        lambda *a, **k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(serve.os.path, "expanduser", lambda p: str(pw))
    monkeypatch.setitem(sys.modules, "arango",
                        type("m", (), {"ArangoClient": _Client}))

    serve.arango_db()
    assert seen.get("username") == "root"
