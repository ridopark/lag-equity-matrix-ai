"""RED for PHASE-1 TASK-1.3 of PLAN-2026-09-09-ingest-coherence: `node_comovement`
must report a loud error -- not a silent "0 edges upserted, done" -- when the
frame `serve.COMOVE_CLOSES()` returns cannot actually answer for the requested
`as_of`. This is the literal reproduction of today's incident: `bars.parquet`
advanced to 2026-09-09 while `bars-10y.parquet` (the file co-movement reads)
stayed at 2026-09-04, and `node_comovement` proceeded anyway, upserting
nothing while reporting success.

`session_available` (PHASE-1 TASK-1.1/1.2, `lagmatrix.comovement`) is checked
*before* `comovement_edges` runs at all -- it cannot see the resulting edge
count, only whether `as_of`'s session and its trailing history are present in
the frame. That is what keeps this failure distinct from a legitimately quiet
trading day producing few or zero edges from a *fully available* window --
`test_node_comovement_upserts_normally_when_history_is_sufficient` below pins
exactly that non-failure case: without it, a `session_available` that always
returns `False` would pass both failure-path tests above it for the wrong
reason.

`upsert_comovement` is replaced with a spy, never a live ArangoDB connection:
this file only needs to prove the failure path never reaches the write, and
this repo never touches the real `lagmatrix` database from a test (D-97's own
lesson -- loaders that write, never drop -- re-applied here to a new writer).
"""

from __future__ import annotations

import pathlib
import sys
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


def _spy(calls):
    def _fake_upsert(db, edges, as_of):
        calls.append((db, edges, as_of))
    return _fake_upsert


def _correlated_closes_ending(last_session: str, n_sessions: int) -> pd.DataFrame:
    """Two correlated columns of engineered returns, `n_sessions` sessions
    ending at `last_session` -- mirrors `test_comovement.py`'s
    `_two_bloc_closes` construction closely enough that a fully-available
    window produces a real, non-zero edge rather than an artefact of an
    otherwise-empty frame.
    """
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.01, n_sessions)
    b = 0.9 * a + rng.normal(0, 0.003, n_sessions)
    idx = pd.bdate_range(end=last_session, periods=n_sessions, tz="UTC")
    return pd.DataFrame(
        {"ZZFAKEA": 100 * np.cumprod(1 + a), "ZZFAKEB": 100 * np.cumprod(1 + b)},
        index=idx,
    )


def test_node_comovement_reports_an_error_when_as_of_exceeds_the_data(monkeypatch):
    """The literal incident: `as_of` (2026-09-09) is five sessions past the
    frame's last session (2026-09-04) -- `bars.parquet` advanced while
    `bars-10y.parquet` (what `COMOVE_CLOSES()` actually returns) did not.

    Falsifies if: `"errors"` is empty, the error names neither 2026-09-09 nor
    2026-09-04, `"done"` is non-empty, or `upsert_comovement` was called.
    """
    import daily_ingest
    import serve

    import lagmatrix.adapters.arango as arango_adapter

    closes = _correlated_closes_ending("2026-09-04", n_sessions=10)
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    monkeypatch.setattr(serve, "arango_db", lambda: object())
    calls = []
    monkeypatch.setattr(arango_adapter, "upsert_comovement", _spy(calls))

    result = daily_ingest.node_comovement({"as_of": "2026-09-09", "dry_run": False})

    assert result.get("errors"), "must report a loud failure, not a silent success"
    assert any("2026-09-09" in e for e in result["errors"])
    assert any("2026-09-04" in e for e in result["errors"])
    assert not result.get("done"), "the failure path must not also claim success"
    assert calls == [], "upsert_comovement must never be reached on the failure path"


def test_node_comovement_reports_an_error_on_insufficient_trailing_history(monkeypatch):
    """`as_of` is present in the frame, but only 10 sessions precede it --
    far short of the `trail=250` `node_comovement` requires. Distinct from
    the test above: this is the "as_of present but history too short" branch,
    not the "as_of absent entirely" one -- both must be caught, but neither
    is allowed to hide behind the other's fixture.

    Falsifies if: `"errors"` is empty, `"done"` is non-empty, or
    `upsert_comovement` was called.
    """
    import daily_ingest
    import serve

    import lagmatrix.adapters.arango as arango_adapter

    idx = pd.bdate_range("2026-01-01", periods=20, tz="UTC")
    as_of = idx[10].date()
    rng = np.random.default_rng(0)
    closes = pd.DataFrame(
        {"ZZFAKEA": 100 + rng.normal(0, 1, 20), "ZZFAKEB": 100 + rng.normal(0, 1, 20)},
        index=idx,
    )
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    monkeypatch.setattr(serve, "arango_db", lambda: object())
    calls = []
    monkeypatch.setattr(arango_adapter, "upsert_comovement", _spy(calls))

    result = daily_ingest.node_comovement({"as_of": as_of.isoformat(), "dry_run": False})

    assert result.get("errors"), "must report a loud failure, not a silent success"
    assert not result.get("done"), "the failure path must not also claim success"
    assert calls == [], "upsert_comovement must never be reached on the failure path"


def test_node_comovement_upserts_normally_when_history_is_sufficient(monkeypatch):
    """Negative control -- the point of this file, not filler. A fixture
    where `as_of` has the full `trail=250` window available must still
    upsert normally. Without this, a `session_available` that always returns
    `False` would pass both failure-path tests above for the wrong reason.
    PHASE-1 is explicit that this case -- a fully-available window, whatever
    its resulting edge count -- must never be caught by the new check.

    Falsifies if: `"done"` is empty, `"errors"` is non-empty, or
    `upsert_comovement` was not called exactly once.
    """
    import daily_ingest
    import serve

    import lagmatrix.adapters.arango as arango_adapter

    n = 250
    closes = _correlated_closes_ending("2026-09-04", n_sessions=n + 1)
    as_of = closes.index[-1].date()
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    monkeypatch.setattr(serve, "arango_db", lambda: object())
    calls = []
    monkeypatch.setattr(arango_adapter, "upsert_comovement", _spy(calls))

    result = daily_ingest.node_comovement({"as_of": as_of.isoformat(), "dry_run": False})

    assert result.get("done"), "a fully-available window must upsert, not fail"
    assert not result.get("errors")
    assert len(calls) == 1, "upsert_comovement must be called exactly once"


def test_node_long_bars_skips_the_fetch_when_already_current(monkeypatch):
    """PHASE-4 TASK-4.1: `node_long_bars` (does not exist yet) must mirror
    `node_bars`'s freshness check against `LONG_BARS`, not `BARS` -- this is
    the fix for tonight's bug, where `LONG_BARS` was defined and never read.

    Falsifies if: `"done"` does not mention "already current", or the spied
    `_run` is called at all.
    """
    import daily_ingest

    monkeypatch.setattr(daily_ingest, "long_bar_date",
                        lambda: datetime.now(UTC).date() - timedelta(days=1))
    calls = []
    monkeypatch.setattr(daily_ingest, "_run", lambda cmd, dry: calls.append(cmd) or "")

    result = daily_ingest.node_long_bars({"dry_run": False})

    assert any("already current" in line for line in result.get("done", []))
    assert calls == [], "_run must never be called when the long file is already current"


def test_node_long_bars_shells_out_to_fetch_daily_bars_when_stale(monkeypatch):
    """The complementary branch of the test above: a `long_bar_date()` more
    than a day old must trigger a real fetch, naming
    `scripts/fetch_daily_bars.py` -- the script `script-fetch` is adding
    concurrently -- not `scripts/fetch_bars.py` (that is `node_bars`'s script,
    for the short file).

    Falsifies if: the spied `_run` is never called, or is called with a
    command that does not name `scripts/fetch_daily_bars.py`.
    """
    import daily_ingest

    monkeypatch.setattr(daily_ingest, "long_bar_date",
                        lambda: datetime.now(UTC).date() - timedelta(days=5))
    calls = []
    monkeypatch.setattr(daily_ingest, "_run", lambda cmd, dry: calls.append(cmd) or "")

    daily_ingest.node_long_bars({"dry_run": False})

    assert calls, "_run must be called when the long bars file is stale"
    assert "scripts/fetch_daily_bars.py" in calls[0]


def test_node_long_bars_never_shells_out_for_real_on_dry_run(monkeypatch):
    """PHASE-4's own "what would make this wrong": a `--dry-run` invocation
    must never shell out to `fetch_daily_bars.py` for real. `node_long_bars`
    must honour `_run`'s existing dry-run contract exactly, the way
    `node_bars` already does -- `_run(cmd, dry=True)` returns a `DRY ...`
    string without ever calling `subprocess.run`.

    `_run` itself is left real here (not spied), unlike the two tests above:
    this test exercises `_run`'s actual dry-run contract, not a stand-in for
    it. Only `subprocess.run` is spied, so a bug that bypassed `_run`'s own
    dry check and called `subprocess.run` directly would still be caught --
    `fetch_daily_bars.py` writes `data/bars-10y.parquet` via a real,
    multi-symbol Alpaca pull, and a dry run that shelled out for real would
    be an expensive, silent way to modify vendor data a "change nothing"
    flag promised not to touch.

    Falsifies if: `subprocess.run` is called at all, or `"done"` does not
    contain a `DRY`-marked line naming `scripts/fetch_daily_bars.py`.
    """
    import subprocess

    import daily_ingest

    monkeypatch.setattr(daily_ingest, "long_bar_date",
                        lambda: datetime.now(UTC).date() - timedelta(days=5))
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))

    result = daily_ingest.node_long_bars({"dry_run": True})

    assert calls == [], "subprocess.run must never be called on a dry run"
    assert any("DRY" in line and "scripts/fetch_daily_bars.py" in line
               for line in result.get("done", []))


def test_as_of_is_resolved_after_the_long_bars_file_is_extended(monkeypatch):
    """PHASE-4 TASK-4.3: the literal ordering bug in today's `main()`, made
    falsifiable. `as_of` must be derived from `serve.default_as_of()` *after*
    `node_long_bars` has run, not before -- today, `main()` calls
    `default_as_of()` at startup, so a run uses the *previous* run's last
    session even after fetching fresh bars.

    A fake `default_as_of` returns a different value on each call: the first
    call stands in for the stale value today's buggy `main()` would have
    captured *before* the long-bars fetch; the second stands in for the
    correct, post-extension value. `node_long_bars` then `node_comovement`
    are invoked directly, in that order (per the plan: plain function calls,
    not through LangGraph). `node_comovement` must make its own call to
    `serve.default_as_of()` -- landing on the second value -- rather than
    reading a value resolved before `node_long_bars` ran.

    Falsifies if: `node_comovement` upserts using the stale (first) value,
    or does not call `serve.default_as_of()` at all (e.g. still requires a
    pre-resolved `state["as_of"]`).
    """
    import daily_ingest
    import serve

    import lagmatrix.adapters.arango as arango_adapter

    values = iter(["2026-09-04", "2026-09-09"])
    monkeypatch.setattr(serve, "default_as_of", lambda: next(values))

    # Stand-in for today's bug: `main()` called `default_as_of()` before the
    # long-bars fetch ran, capturing the stale ("before") value.
    stale_as_of = serve.default_as_of()
    assert stale_as_of == "2026-09-04"

    monkeypatch.setattr(daily_ingest, "long_bar_date", lambda: datetime.now(UTC).date())
    monkeypatch.setattr(daily_ingest, "_run", lambda cmd, dry: "")

    n = 250
    closes = _correlated_closes_ending("2026-09-09", n_sessions=n + 1)
    monkeypatch.setattr(serve, "COMOVE_CLOSES", lambda: closes)
    monkeypatch.setattr(serve, "arango_db", lambda: object())
    upsert_calls = []
    monkeypatch.setattr(arango_adapter, "upsert_comovement", _spy(upsert_calls))

    daily_ingest.node_long_bars({"dry_run": True})
    result = daily_ingest.node_comovement({"dry_run": False})

    assert upsert_calls, f"node_comovement must have upserted: {result}"
    used_as_of = upsert_calls[0][2]
    assert used_as_of == date(2026, 9, 9), (
        "node_comovement must resolve as_of from the *second* call to "
        f"serve.default_as_of(), after node_long_bars runs -- got {used_as_of}"
    )
