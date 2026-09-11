"""RED: `followers()` and `network()` must consult `session_available` before
calling `comovement_edges`, instead of quietly returning an empty-but-successful
payload when `as_of` cannot actually be answered from the frame.

`comovement_edges(closes, as_of, trail)` returns `[]` -- not an error -- both
when `as_of`'s session is absent from `closes.index` and when fewer than
`trail` sessions precede it (`src/lagmatrix/comovement.py:75-79`). `followers()`
and `network()` (`scripts/serve.py`) pass that empty list straight through into
a normal-looking payload (`followers_found: 0`, `edges: []`), which
`serve_index.html:1093` renders as "No company has moved with X reliably
enough ... some names really do move on their own" -- a claim about the
market that is actually a claim about the frame not covering the date asked
for. Measured directly against the live service for DELL (which has 4 real
followers on a covered session): a Saturday, tomorrow (2026-09-11, newer than
the frame's last session), and a date inside the first 250 sessions all come
back `{"followers_found": 0, "error": None}`.

`lagmatrix.comovement.session_available(closes, as_of, trail) -> (ok, reason)`
already exists for exactly this (written for `daily_ingest.py:195`, PHASE-1 of
PLAN-2026-09-09-ingest-coherence) and returns reasons like "2026-09-11 not
found in data (last session available: 2026-09-10)" and "only 42 sessions
precede 2016-09-20, need 250". `followers()`/`network()` do not call it yet.

The target shape is `{"error": <reason>}` -- not a new contract. `followers()`
already returns exactly that shape for an unknown symbol
(`return {"error": f"{sym} not in the price file"}`), and
`serve_index.html:1093` already checks `d.error` first, before it ever reads
`d.followers`. So the fix needs no UI change; these tests pin the same
existing shape being reached from a second cause.

Frames are synthetic, built in-test with `pd.bdate_range` -- `data/bars.
parquet` and `data/bars-10y.parquet` are never read, and neither ArangoDB nor
postgres is touched, per the assignment's laptop-safe constraint.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

TRAIL = 5


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


def _correlated_closes() -> pd.DataFrame:
    """15 business-day sessions ending 2026-01-30, two columns engineered to
    correlate at ~0.9 (well above the 0.5 default threshold) -- mirrors
    `test_daily_ingest.py`'s `_correlated_closes_ending` construction so a
    fully-available window here produces a real, non-empty follower list
    rather than an artefact of an otherwise-empty frame.
    """
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.01, 15)
    b = 0.9 * a + rng.normal(0, 0.003, 15)
    idx = pd.bdate_range(end="2026-01-30", periods=15, tz="UTC")
    return pd.DataFrame(
        {"ZZFAKEA": 100 * np.cumprod(1 + a), "ZZFAKEB": 100 * np.cumprod(1 + b)},
        index=idx,
    )


def test_followers_returns_error_when_as_of_is_not_a_session(monkeypatch):
    """`as_of` is a date the frame has never heard of (30 days past its last
    session) -- the "picked a date newer than the data" case from the report.

    Falsifies if: the result has no `"error"` key (e.g. it instead reports
    `followers_found: 0` with `error` absent or `None`).
    """
    import serve

    closes = _correlated_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    missing = closes.index[-1].date() + timedelta(days=30)

    result = serve.followers("ZZFAKEA", missing.isoformat(), trail=TRAIL)

    assert "error" in result and result["error"], (
        f"expected an error for an as_of the frame does not contain, got {result!r}"
    )


def test_followers_returns_error_when_fewer_than_trail_sessions_precede_as_of(monkeypatch):
    """`as_of` is a real session, but only 3 sessions precede it -- short of
    `trail=5`. Distinct from the test above: this is the "as_of present but
    history too short" branch, not the "as_of absent" one.

    Falsifies if: the result has no `"error"` key.
    """
    import serve

    closes = _correlated_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    short_history = closes.index[3].date()

    result = serve.followers("ZZFAKEA", short_history.isoformat(), trail=TRAIL)

    assert "error" in result and result["error"], (
        f"expected an error for insufficient trailing history, got {result!r}"
    )


def test_network_returns_error_when_as_of_is_not_a_session(monkeypatch):
    """The same not-a-session case as the first `followers()` test, against
    `network()` -- the report names both endpoints, not just one.

    Falsifies if: the result has no `"error"` key.
    """
    import serve

    closes = _correlated_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    missing = closes.index[-1].date() + timedelta(days=30)

    result = serve.network("ZZFAKEA", missing.isoformat(), trail=TRAIL)

    assert "error" in result and result["error"], (
        f"expected an error for an as_of the frame does not contain, got {result!r}"
    )


def test_followers_succeeds_on_a_valid_session_with_enough_history(monkeypatch):
    """Positive control. `as_of` is a real session with `trail=5` sessions
    genuinely preceding it and a pair correlated at ~0.9 -- the case that
    must keep working. Without this test, an implementation that returns
    `{"error": ...}` unconditionally would pass every test above for the
    wrong reason.

    Falsifies if: the result contains an `"error"` key, or `"followers"` is
    empty.
    """
    import serve

    closes = _correlated_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    valid = closes.index[10].date()

    result = serve.followers("ZZFAKEA", valid.isoformat(), trail=TRAIL)

    assert "error" not in result, f"a fully-available window must not error, got {result!r}"
    assert result["followers"], "ZZFAKEA/ZZFAKEB were engineered to correlate at ~0.9"


def test_followers_error_text_comes_from_session_available_not_a_local_copy(monkeypatch):
    """The reason string must be `session_available`'s own return value, not
    a second, hand-written copy of the same sentence living in `serve.py` --
    the two would drift the moment one of them is edited alone.

    Falsifies if: `result["error"]` differs at all from what
    `session_available` returns for the identical `(closes, as_of, trail)`.
    """
    import serve

    from lagmatrix.comovement import session_available

    closes = _correlated_closes()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    serve.ALLOW_REAL = True
    missing = closes.index[-1].date() + timedelta(days=30)

    result = serve.followers("ZZFAKEA", missing.isoformat(), trail=TRAIL)

    _, expected_reason = session_available(closes, missing, TRAIL)
    assert result.get("error") == expected_reason
