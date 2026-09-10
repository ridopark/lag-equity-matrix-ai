"""RED: `default_as_of()` offers a date the feature it feeds cannot answer.

Measured directly on this machine before writing these tests (not inferred):

    data/bars.parquet      last session 2026-09-09, 3,201 symbols
    data/bars-10y.parquet  last session 2026-09-04, 2,183 symbols

    serve.default_as_of()                          -> "2026-09-09"
    serve.followers("PANW", "2026-09-04")          -> followers_found=13, error=None
    serve.followers("PANW", default_as_of())       -> followers_found=0,  error=None

`default_as_of()` (`scripts/serve.py:75-93`) reads only `data/bars.parquet`.
Every co-movement feature -- `/followers`, `/network`, and `CoMovementFollowers`
in `load()` -- reads `COMOVE_CLOSES()` (`scripts/serve.py:55-72`) instead, which
prefers `data/bars-10y.parquet`. The two files diverged after today's first
real `daily_ingest.py` run, so the page's own default date now silently
answers nothing for the feature it exists to demo, with no error surfaced.

Tests only, per this phase's scope; `src/` and `scripts/` are untouched.
`data/bars.parquet` and `data/bars-10y.parquet` are both present on this
machine (confirmed via `ls -la data/` and a direct read before writing this
file, and again immediately before running the suite below), so the first two
tests below run for real rather than skipping -- see Q-43 in
`docs/spikes/overall.md` for why a silent skip here would be exactly the
failure this project already got burned by once. They gate on the files'
presence only so a machine without real market data does not error instead of
skip; that gate was verified false (i.e. not taken) on this machine.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import date

import pandas as pd
import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

BARS = pathlib.Path("data/bars.parquet")
BARS_10Y = pathlib.Path("data/bars-10y.parquet")


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


@pytest.fixture(autouse=True)
def _reset_comove_cache(_scripts_on_path):
    """`COMOVE_CLOSES()` memoises in a module-level dict; clear it so one
    test's monkeypatched `pd.read_parquet` cannot leak a stale frame into the
    next."""
    import serve

    serve._COMOVE_CACHE.clear()
    yield
    serve._COMOVE_CACHE.clear()


def _require_real_data():
    if not (BARS.exists() and BARS_10Y.exists()):
        pytest.skip(
            "data/bars.parquet and data/bars-10y.parquet not present on this machine"
        )


# ---------------------------------------------------------------------------
# The defect: default_as_of() must answer a date COMOVE_CLOSES() can use.
# ---------------------------------------------------------------------------

def test_default_as_of_date_is_present_in_comove_closes_index():
    """The invariant, not the constant: whatever session `default_as_of()`
    returns must be a session `COMOVE_CLOSES()` actually has, since that is
    the frame every co-movement feature reads. Pinning today's value
    ('2026-09-04') would rot the moment the next ingest runs; this instead
    stays true across ingests.

    Falsifies if: `default_as_of()`'s date is absent from `COMOVE_CLOSES()`'s
    index -- which it is today, since `default_as_of()` reads only
    `data/bars.parquet` (last session 2026-09-09) while `COMOVE_CLOSES()`
    prefers `data/bars-10y.parquet` (last session 2026-09-04).
    """
    _require_real_data()
    import serve

    closes = serve.COMOVE_CLOSES()
    d = date.fromisoformat(serve.default_as_of())
    assert d in closes.index.date


def test_followers_at_default_as_of_returns_a_non_empty_result():
    """The user-visible consequence, not just the mechanism: asking
    `/followers` for the page's own default date must not come back empty for
    a leader known to have followers. PANW has followers at 2026-09-04 (the
    last session `COMOVE_CLOSES()` has) but none at 2026-09-09
    (`default_as_of()`'s answer today), with no error reported either way --
    the page silently shows nothing and looks fine doing it.

    Falsifies if: `followers_found` is 0 (or `error` is set) when queried at
    `default_as_of()`. A fix that satisfies the index-membership assertion
    above while still returning a date `/followers` cannot use would still
    fail this.
    """
    _require_real_data()
    import serve

    serve.ALLOW_REAL = True
    result = serve.followers("PANW", serve.default_as_of())
    assert result.get("error") is None
    assert result["followers_found"] > 0


# ---------------------------------------------------------------------------
# Guards: the existing fallback chain must survive whatever fixes the above.
# ---------------------------------------------------------------------------

def test_default_as_of_falls_back_to_synthetic_fixture_when_real_files_absent(monkeypatch):
    """A fresh checkout with no real market data must still get a date, from
    the committed synthetic fixture -- this must keep working after the fix
    above changes which real file(s) `default_as_of()` reads.

    Falsifies if: this raises, or returns anything other than the synthetic
    fixture's last session.
    """
    import serve

    real_read_parquet = pd.read_parquet

    def _fake_read_parquet(path, *a, **kw):
        if str(path) == serve.SYNTHETIC_CLOSES:
            return real_read_parquet(path, *a, **kw)
        raise FileNotFoundError(path)

    monkeypatch.setattr(serve.pd, "read_parquet", _fake_read_parquet)

    fixture = real_read_parquet(serve.SYNTHETIC_CLOSES)
    expected_idx = (
        fixture.pivot_table(index="timestamp", columns="symbol", values="close").index
        if "timestamp" in fixture.columns else fixture.index
    )
    assert serve.default_as_of() == str(expected_idx[-1].date())


def test_default_as_of_falls_back_to_constant_when_no_price_file_readable(monkeypatch):
    """A machine with neither real data nor a readable synthetic fixture must
    still return something rather than crash -- the old baked-in constant.

    Falsifies if: this raises, instead of returning `SYNTHETIC_FALLBACK_DATE`.
    """
    import serve

    def _always_fails(path, *a, **kw):
        raise FileNotFoundError(path)

    monkeypatch.setattr(serve.pd, "read_parquet", _always_fails)

    assert serve.default_as_of() == serve.SYNTHETIC_FALLBACK_DATE
