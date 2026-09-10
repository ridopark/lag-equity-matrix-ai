"""RED: `serve.neighbourhood`'s `as_of` has no point-in-time guard at all.

Every other endpoint in `scripts/serve.py` (`movers`, `followers`, `network`,
`load`) parses its `as_of` with `date.fromisoformat` before using it.
`neighbourhood(symbol, as_of)` (`scripts/serve.py:503`) is the one that does
not: the raw string goes straight into `cap.TRAVERSAL_AQL`'s bind vars, where
`capture_showcase.py:54`'s `FILTER p.edges[*].filing_date ALL <= @as_of` and
`:96`'s equivalent on `first_seen` compare it as a string. An ISO string
compares correctly against another ISO string and arbitrarily against
anything else, so a malformed `as_of` silently disables the guard rather than
raising -- measured live against `lagmatrix`/WMT before this file was
written: `'2019-01-01'` (correct ISO) returns 2 edges, `'2019-1-1'` (merely
unpadded) returns 8, `'Jan 1 2019'` and `'zzzz'` both return the full
present-day 13.

This is D-82's exact class (`docs/spikes/overall.md`) -- the vector
retriever's missing `as_of < `-bound leaked 24 days of future news for the
identical reason: a guard that exists in a docstring/convention rather than
by construction. It is also the exact violation D-16 built the graph to
avoid: "point-in-time correctness by construction (both edge types are
computed from observations timestamped at or before t)" -- `neighbourhood`
is the one caller that does not honour "at or before t" because it never
establishes what `t` is.

Seam: `neighbourhood(symbol, as_of)` directly, never through `do_GET` (which
has no unit-testable seam of its own -- see `test_serve_params.py`'s
docstring for why). `symbol` shape is deliberately NOT re-validated here:
`neighbourhood` has exactly one caller, the `/graph` handler
(`scripts/serve.py:620`), which already applies `.isalnum()` at `:616-617`
before calling it (confirmed by grep -- nothing else in `scripts/` or `src/`
calls this function). Adding a second `.isalnum()` inside `neighbourhood`
itself would be a guard duplicated for its own sake (CLAUDE.md, DRY); it
would only be justified if some other caller could reach the function
unguarded, which none does.

`neighbourhood` reaches for the real `lagmatrix` database via the
module-level `arango_db()` with no injection point of its own, and this
project's `lagmatrix` collections (`equity`, `supplies_to`, `co_mentioned`,
`article`) must never be written to by a test. The smallest fix that avoids
either touching `lagmatrix` or requiring a new constructor parameter on
`neighbourhood` (which nothing else would ever call) is to monkeypatch the
module-level `serve.arango_db` to return a throwaway, self-seeded database
instead -- the same database-level isolation `test_arango_topology.py`
already uses for `ArangoTopology`, one level up the call stack.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

from conftest import arango_db_or_skip

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

DB_NAME = "test_asof_neighbourhood"
VERTEX = "equity"
SUPPLY_EDGE = "supplies_to"
COMENTION_EDGE = "co_mentioned"

# Entirely synthetic symbols, not real tickers (test_arango_topology.py's
# rationale applies identically: the real supplies_to edges are mid-audit
# per D-78 and are not a safe stand-in even read-only).
CANDIDATE = "TOPX"
SUPPLIER_EARLY = "SUPOLD"   # filed before the early as_of cutoff
SUPPLIER_LATE = "SUPNEW"    # filed after the early cutoff, before the late one

EARLY_FILING = "2018-06-01"
LATE_FILING = "2022-01-01"

AS_OF_EARLY = "2019-01-01"   # after EARLY_FILING, before LATE_FILING
AS_OF_LATE = "2025-01-01"    # after both filings


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


@pytest.fixture(scope="module")
def seeded_db():
    """A throwaway database seeded with one supply-chain fan-in, never
    `lagmatrix` (see module docstring)."""
    db = arango_db_or_skip(DB_NAME)

    if not db.has_collection(VERTEX):
        db.create_collection(VERTEX)
    if not db.has_collection(SUPPLY_EDGE):
        db.create_collection(SUPPLY_EDGE, edge=True)
    if not db.has_collection(COMENTION_EDGE):
        db.create_collection(COMENTION_EDGE, edge=True)
    vertices = db.collection(VERTEX)
    supplies_to = db.collection(SUPPLY_EDGE)
    co_mentioned = db.collection(COMENTION_EDGE)
    vertices.truncate()
    supplies_to.truncate()
    co_mentioned.truncate()  # collection must exist for COMENTION_AQL, stays empty

    for symbol in [CANDIDATE, SUPPLIER_EARLY, SUPPLIER_LATE]:
        vertices.insert({"_key": symbol, "symbol": symbol})
    supplies_to.insert({
        "_from": f"{VERTEX}/{SUPPLIER_EARLY}", "_to": f"{VERTEX}/{CANDIDATE}",
        "filing_date": EARLY_FILING,
    })
    supplies_to.insert({
        "_from": f"{VERTEX}/{SUPPLIER_LATE}", "_to": f"{VERTEX}/{CANDIDATE}",
        "filing_date": LATE_FILING,
    })
    return db


@pytest.fixture
def patched_serve(seeded_db, monkeypatch):
    """`serve` with its module-level `arango_db()` redirected to the
    throwaway database above -- the injection point the module docstring
    explains, not a change to `scripts/serve.py` itself."""
    import serve

    monkeypatch.setattr(serve, "arango_db", lambda: seeded_db)
    return serve


# ---------------------------------------------------------------------------
# The defect: a malformed `as_of` must raise, not query.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_as_of", [
    "2019-1-1",       # unpadded -- measured live to return 8 edges instead of 2
    "Jan 1 2019",     # not ISO at all -- measured live to return all 13
    "zzzz",           # not a date -- measured live to return all 13
    "01/01/2019",     # a different, equally natural date format
    "",                # empty -- no fallback to a default exists in this function
])
def test_malformed_as_of_raises_value_error_instead_of_querying(bad_as_of, patched_serve):
    """Falsifies if `neighbourhood` returns a dict (i.e. runs the query)
    instead of raising `ValueError` for any of these five inputs."""
    with pytest.raises(ValueError):
        patched_serve.neighbourhood(CANDIDATE, bad_as_of)


def test_malformed_as_of_error_names_the_bad_value(patched_serve):
    """The caller needs to know which value was rejected.

    Falsifies if the raised `ValueError`'s message does not contain the
    literal bad string `'zzzz'`.
    """
    with pytest.raises(ValueError, match=re.escape("zzzz")):
        patched_serve.neighbourhood(CANDIDATE, "zzzz")


def test_malformed_as_of_never_reaches_the_aql_query(seeded_db, monkeypatch):
    """Stronger than "raises somewhere": the AQL traversal must never run at
    all for a malformed `as_of` -- validation must come before the query, not
    be recovered from after it runs.

    Falsifies if `db.aql.execute` is called even once before the `ValueError`
    is raised.
    """
    import serve

    calls = []
    monkeypatch.setattr(seeded_db.aql, "execute",
                         lambda *a, **k: (calls.append((a, k)), iter([]))[1])
    monkeypatch.setattr(serve, "arango_db", lambda: seeded_db)

    with pytest.raises(ValueError):
        serve.neighbourhood(CANDIDATE, "zzzz")

    assert calls == []


def test_as_of_with_a_time_component_is_rejected_not_accepted_by_luck(patched_serve):
    """`'2019-01-01T00:00:00'` currently returns the *correct* 2-edge result
    against live `lagmatrix`/WMT today -- but only because ISO-string
    comparison happens to still order it correctly against plain `YYYY-MM-DD`
    filing dates, the same coincidence that makes every other malformed input
    above "work" for the wrong reason. Every other endpoint in this file
    (`movers`, `followers`, `network`, `load`) already rejects this exact
    string via `date.fromisoformat` -- confirmed directly against this
    interpreter (Python 3.12.3) before writing this test, not assumed:
    `date.fromisoformat('2019-01-01T00:00:00')` raises `ValueError` here,
    because `date.fromisoformat` (unlike `datetime.fromisoformat`) never
    accepts a time component. `neighbourhood` adopting the same parser is
    therefore consistent with the rest of the module, not a new, stricter
    rule invented just for this function.

    Falsifies if this does not raise `ValueError`.
    """
    with pytest.raises(ValueError):
        patched_serve.neighbourhood(CANDIDATE, "2019-01-01T00:00:00")


# ---------------------------------------------------------------------------
# Guard: a well-formed `as_of` must keep working, and keep filtering -- the
# test that gives the fix meaning rather than merely accepting more strings.
# ---------------------------------------------------------------------------

def test_well_formed_as_of_still_filters_the_traversal(patched_serve):
    """`AS_OF_EARLY` sits after `SUPPLIER_EARLY`'s filing and before
    `SUPPLIER_LATE`'s; `AS_OF_LATE` sits after both. The point-in-time guard
    is only proven live, not merely proven to parse, by showing the earlier
    date actually excludes the later filing.

    Falsifies if `AS_OF_EARLY` does not return exactly `{SUPPLIER_EARLY}`, if
    `AS_OF_LATE` does not return both suppliers, or if the early result is
    not strictly fewer edges than the late one.
    """
    early = patched_serve.neighbourhood(CANDIDATE, AS_OF_EARLY)
    late = patched_serve.neighbourhood(CANDIDATE, AS_OF_LATE)

    assert {r["symbol"] for r in early["edges"]} == {SUPPLIER_EARLY}
    assert {r["symbol"] for r in late["edges"]} == {SUPPLIER_EARLY, SUPPLIER_LATE}
    assert len(early["edges"]) < len(late["edges"])
