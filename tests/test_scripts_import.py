"""Every script under `scripts/` must import cleanly.

Nothing under `tests/` imports these modules, and they are not part of the
collected package, so a broken import in any of them leaves the suite fully
green. That is not hypothetical, twice over:

  - Deleting `assessor.rank_by_room` (D-87) broke `serve.py`'s import and the
    suite still reported 111 passed; only a manual run caught it (Q-43).
  - D-101's fastembed swap removed `scipy` -- never a declared dependency, it
    arrived transitively via sentence-transformers -- and both of D-95's
    reproduction scripts stopped importing entirely. The suite was green at 248,
    ruff was clean, and nobody noticed until one was run by hand (D-116).

The list is **discovered, not enumerated**, because the failure mode is
forgetting: the second incident happened to scripts that existed for weeks and
had never been added to a hardcoded list. A new script is covered the moment it
is written.

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


SCRIPT_NAMES = sorted(p.stem for p in SCRIPTS.glob("*.py") if not p.stem.startswith("_"))


def test_discovery_found_the_scripts():
    """A guard on the guard: if the glob ever returns nothing -- a moved
    directory, a renamed folder -- every import test below would vacuously
    pass. Falsifies if discovery breaks silently."""
    assert len(SCRIPT_NAMES) > 20, f"only discovered {SCRIPT_NAMES}"


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_script_imports_cleanly(name):
    """Falsifies if the module raises on import — a deleted function another
    script still imports, or a dependency that quietly left the lockfile.

    Import-only on purpose. These scripts must do no work at module scope: no
    server, no socket, no market data, no ArangoDB. This test is also the check
    that that stays true, so a script that starts doing work at import time
    fails here rather than at 3am."""
    __import__(name)


def test_serve_exposes_the_entry_points_the_page_depends_on():
    """`serve.py`'s HTTP handler dispatches to these by name. A rename that
    missed a call site would otherwise only surface in a browser.

    Falsifies if any is missing or is not callable.
    """
    import serve

    for fn in ("stream", "load", "neighbourhood", "arango_db", "main"):
        assert callable(getattr(serve, fn, None)), f"serve.{fn} is missing or not callable"
