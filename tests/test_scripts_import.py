"""The two scripts nothing else imports.

`scripts/serve.py` and `scripts/capture_showcase.py` are not part of the
collected package, so nothing under `tests/` has ever imported them. A broken
import in either — a deleted function still being imported, a renamed field —
left the suite fully green. That is not hypothetical: deleting
`assessor.rank_by_room` (D-87) broke `serve.py`'s import and the suite still
reported 111 passed, with only a manual run catching it (Q-43).

These are deliberately import-only. They do not start a server, open a socket,
read market data or touch ArangoDB — `serve.py` does all of that inside
functions, never at module scope, and this test is the check that it stays
that way.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


@pytest.mark.parametrize(
    "name", ["serve", "capture_showcase", "capture_trace", "fetch_daily_bars"]
)
def test_script_imports_cleanly(name):
    """Falsifies if the module raises on import — the exact failure that
    deleting a function another script still imports would produce."""
    __import__(name)


def test_serve_exposes_the_entry_points_the_page_depends_on():
    """`serve.py`'s HTTP handler dispatches to these by name. A rename that
    missed a call site would otherwise only surface in a browser.

    Falsifies if any is missing or is not callable.
    """
    import serve

    for fn in ("stream", "load", "neighbourhood", "arango_db", "main"):
        assert callable(getattr(serve, fn, None)), f"serve.{fn} is missing or not callable"
