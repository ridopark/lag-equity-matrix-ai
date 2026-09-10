"""RED: HTTP query parameters must be validated before reaching computation.

`scripts/serve.py`'s `do_GET` currently parses `min_abs_corr`, `top_n` and
`limit` with bare `float()`/`int()` (serve.py:545, :557, :569, :601) and lets
whatever comes out flow straight into `network()`/`followers()`/`stream()`.
A query string is attacker-controlled: `?min_abs_corr=nan` parses cleanly
(`float("nan")` does not raise) and then behaves exactly like `min_abs_corr=0`
inside `comovement_edges` (`abs(c) < nan` is always `False`), driving a
~100-second, ~3.5GB computation from a single GET. This machine also runs the
owner's real-money trading system on the same node, so that is not a
performance nuisance -- it is a neighbour-affecting OOM risk. (See
`tests/test_comovement.py`'s new floor tests for the layer-1 half of this
fix, inside `comovement_edges` itself; this file is layer 2 -- the handler's
own validation, which must not rely on layer 1 alone since `min_abs_corr` is
not the only unbounded parameter, and `top_n`/`limit` never reach
`comovement_edges` at all.)

`do_GET` is not callable from a unit test without a real listening socket
(`BaseHTTPRequestHandler` reads its request off `self.rfile`/writes to
`self.wfile`, wired up by `socketserver` at connection time), and this repo
has already been bitten once by a live-server test that silently skipped
instead of failing (Q-43, `tests/conftest.py`'s `_unavailable`). So this
tests the smallest extractable seam instead: `parse_bounded`, a function
`serve.py` does not yet have. Introduced here for testability, per the
tdd-red brief -- `do_GET` is expected to call it once per query parameter
and turn its `ValueError` into `self.send_error(400, ...)`, but that wiring
itself is not exercised by this file.

The three call sites' intended bounds (not yet wired into `serve.py`; there
is no existing pinned value to derive these from, so they are a policy call
made here for the green phase to implement verbatim):

    min_abs_corr  (0.1, 1.0)   -- 0.1 matches the live UI slider's own
                                  minimum (`scripts/serve_index.html:268`,
                                  `min="0.1"`); 1.0 is the mathematical
                                  ceiling of a correlation.
    top_n         (1, 500)     -- current defaults are 25/25/40; 500 is
                                  generous headroom over any real UI table,
                                  not a measurement of "the" right number.
    limit         (1, 1000)    -- `/run` drives real per-candidate pipeline
                                  work per unit of `limit`, unlike `top_n`
                                  (a post-hoc slice); capped well above any
                                  exercised usage.

D-102 also flagged `/run`'s `source`/leader-symbol validation (`load()` has
no `.isalnum()` guard equivalent to the other three endpoints, and silently
falls through to the synthetic default for an unrecognised `source`). That
is covered in `tests/test_serve_source.py` instead of here -- written
independently and in more depth (verified against real `data/bars-10y.
parquet`, asserts the raised message names the bad value, and pins that a
legitimate `leader:<real symbol>` call keeps working) -- so it is not
duplicated in this file.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

MIN_ABS_CORR_BOUNDS = (0.1, 1.0)
TOP_N_BOUNDS = (1, 500)
LIMIT_BOUNDS = (1, 1000)


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


def test_parse_bounded_returns_the_cast_value_when_within_range():
    """The non-adversarial case: a legitimate `min_abs_corr=0.5` must parse
    to exactly `0.5`, not merely "some truthy value" -- pins both the cast
    and that in-range values pass through unchanged.

    Falsifies if: this raises, or returns anything other than `0.5`.
    """
    import serve

    assert serve.parse_bounded("0.5", float, *MIN_ABS_CORR_BOUNDS) == 0.5


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_parse_bounded_rejects_non_finite_query_strings(raw):
    """The exact attack string identified above: `?min_abs_corr=nan` (or
    `inf`/`-inf`) parses without a `float()` exception, so it must be caught
    here -- not left to silently reach `comovement_edges` as an unbounded
    threshold.

    Falsifies if: this does not raise `ValueError`.
    """
    import serve

    with pytest.raises(ValueError):
        serve.parse_bounded(raw, float, *MIN_ABS_CORR_BOUNDS)


def test_parse_bounded_rejects_a_value_below_the_floor():
    """`min_abs_corr=0` is the exact value measured (see module docstring)
    to produce ~2.38 million edges from ~100s of CPU; it must be rejected
    at the handler, not merely relied on to be clamped one layer down in
    `comovement_edges`.

    Falsifies if: this does not raise `ValueError`.
    """
    import serve

    with pytest.raises(ValueError):
        serve.parse_bounded("0", float, *MIN_ABS_CORR_BOUNDS)


def test_parse_bounded_rejects_a_value_above_the_ceiling():
    """`top_n=1000000` is not a real request -- no UI ever asks for more
    than a few hundred rows -- and must not be forwarded as a legitimate
    slice bound.

    Falsifies if: this does not raise `ValueError`.
    """
    import serve

    with pytest.raises(ValueError):
        serve.parse_bounded("1000000", int, *TOP_N_BOUNDS)


@pytest.mark.parametrize("raw", ["abc", "", "3.5"])
def test_parse_bounded_rejects_a_malformed_int_literal(raw):
    """`limit` is meant to be a plain positive integer; `int()` already
    raises on `"abc"`/`""`/`"3.5"` on its own, and this must not swallow
    that into some silent default -- it must surface as the same
    `ValueError` a caller turns into a 400.

    Falsifies if: this does not raise `ValueError`.
    """
    import serve

    with pytest.raises(ValueError):
        serve.parse_bounded(raw, int, *LIMIT_BOUNDS)


def test_parse_bounded_rejects_a_negative_int():
    """A negative `limit` (or `top_n`) is nonsensical -- there is no "run
    negative five candidates" -- and must be rejected rather than silently
    reinterpreted (e.g. as a from-the-end slice).

    Falsifies if: this does not raise `ValueError`.
    """
    import serve

    with pytest.raises(ValueError):
        serve.parse_bounded("-5", int, *LIMIT_BOUNDS)
