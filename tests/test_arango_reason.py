"""`arango_db()` must say *why* it could not connect (Q-55).

It returns None for every failure and callers render "ArangoDB not reachable".
During D-117's deployment the pod could not read the mounted password file --
Kubernetes owns secret volumes root:root and the container runs as uid 10001 --
and that PermissionError was reported as a network problem. The database was
reachable throughout. The message was loud and named the wrong cause, so it
sent the search to the URL, which was changed for nothing.

Returning None rather than raising stays: the correlation half of the pipeline
needs no database, and a laptop with no tunnel should still serve the page
(that is the docstring's stated intent). What must change is that the *reason*
survives. The socket probe already distinguishes unreachable from everything
else -- the information exists and is thrown away.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


def _stub_client(exc):
    class _Client:
        def __init__(self, hosts): pass
        def db(self, *a, **k):
            if exc:
                raise exc
            return "db-handle"
    return type("m", (), {"ArangoClient": _Client})


def _reachable(monkeypatch, serve):
    monkeypatch.setattr(serve.socket, "create_connection",
                        lambda *a, **k: contextlib.nullcontext())


def test_reason_is_empty_after_a_successful_connect(monkeypatch, tmp_path):
    import serve
    pw = tmp_path / "pw"
    pw.write_text("secret\n")
    _reachable(monkeypatch, serve)
    monkeypatch.setattr(serve.os.path, "expanduser", lambda p: str(pw))
    monkeypatch.setitem(sys.modules, "arango", _stub_client(None))
    assert serve.arango_db() == "db-handle"
    assert serve.arango_reason() == ""


def test_unreachable_socket_says_unreachable(monkeypatch):
    """The one case the old message was right about."""
    import serve

    def _refuse(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(serve.socket, "create_connection", _refuse)
    assert serve.arango_db() is None
    assert "unreachable" in serve.arango_reason().lower()


def test_unreadable_credential_does_not_claim_unreachable(monkeypatch, tmp_path):
    """The D-117 failure. Falsifiable: fails if the reason says "unreachable"
    for a PermissionError on the password file, which is what sent the search
    to the URL while the database was reachable."""
    import serve
    pw = tmp_path / "pw"
    pw.write_text("secret\n")
    _reachable(monkeypatch, serve)
    monkeypatch.setattr(serve.os.path, "expanduser", lambda p: str(pw))

    def _denied(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(pathlib.Path, "read_text", _denied)

    assert serve.arango_db() is None
    reason = serve.arango_reason()
    assert "unreachable" not in reason.lower(), (
        f"a PermissionError on the credential was reported as {reason!r}")
    assert "permission" in reason.lower() or "credential" in reason.lower()


def test_auth_failure_is_distinguishable_from_both(monkeypatch, tmp_path):
    """A wrong password is neither unreachable nor unreadable, and an operator
    needs to tell those apart at 3am."""
    import serve
    pw = tmp_path / "pw"
    pw.write_text("wrong\n")
    _reachable(monkeypatch, serve)
    monkeypatch.setattr(serve.os.path, "expanduser", lambda p: str(pw))
    monkeypatch.setitem(sys.modules, "arango",
                        _stub_client(RuntimeError("[HTTP 401][ERR 11] bad username/password")))
    assert serve.arango_db() is None
    reason = serve.arango_reason()
    assert "unreachable" not in reason.lower()
    assert "401" in reason or "username" in reason.lower()
