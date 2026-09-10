"""RED: persisting co-movement edges into ArangoDB (D-95's numbers), so the
daily job can write what `comovement_edges()` measures and the pipeline can
read it back later.

`upsert_comovement(db, edges, as_of)` and `movers_with(db, symbol, as_of,
min_abs_corr=0.5)` do not exist yet in `lagmatrix.adapters.arango` -- only
`ArangoTopology` (the `equity`/`supplies_to` reader) lives there today. The
import below is expected to raise `ImportError`; that is the RED this file
exists to produce.

Storage choices this file pins down (not yet decided in source):

- One edge collection, `moves_with`, holding one document per
  `ComovementEdge` as measured for a given `as_of` -- keyed deterministically
  (e.g. `f"{a}->{b}|{as_of}"`) so a re-run for the same date replaces rather
  than duplicates. This is D-97's lesson (the drop-and-rebuild loaders that
  destroyed 47,640 embeddings) applied to a new writer: `upsert_comovement`
  must survive being called twice with the same input.
- Co-movement is symmetric (D-95), but only ONE direction is stored per pair
  -- whichever order `comovement_edges()` produced. `movers_with` must find a
  symbol whether it was stored as the edge's `a` or its `b`; storing both
  directions would double the collection for no query this pipeline makes.
- Point-in-time (D-16, and D-82's lookahead lesson): an edge measured `as_of`
  a later date must never come back when reading `as_of` an earlier one, even
  when both edges involve the same queried symbol.

Live-gated via `arango_db_or_skip`, against a throwaway database of its own
(`test_comovement_store`) -- never `lagmatrix` and never its real `equity`,
`supplies_to`, `co_mentioned`, `article` collections.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import arango_db_or_skip
from lagmatrix.adapters.arango import movers_with, upsert_comovement
from lagmatrix.domain.models import ComovementEdge

ARANGO_DB_NAME = "test_comovement_store"
EDGE = "moves_with"

AS_OF = date(2024, 3, 1)
EARLY = date(2024, 1, 1)
LATE = date(2024, 6, 1)


@pytest.fixture
def db():
    """A throwaway database of its own, with a clean `moves_with` collection
    per test -- never `lagmatrix`."""
    d = arango_db_or_skip(ARANGO_DB_NAME)
    if not d.has_collection(EDGE):
        d.create_collection(EDGE, edge=True)
    d.collection(EDGE).truncate()
    return d


def test_round_trip_preserves_the_stored_numbers(db):
    """Write one edge, read it back for one of its two symbols, and check
    every measured field survives the trip.

    Falsifies if: `upsert_comovement`/`movers_with` don't exist yet (today's
    state -- ImportError at collection), or if any of `corr`, `n_sessions`,
    `ci_low`, `ci_high`, `flag` comes back different from what was written
    (named-attribute checks, not whole-model equality, since pydantic v2's
    default `extra="ignore"` would make a bare `==` comparison pass even if
    an unrelated field silently diverged).
    """
    edge = ComovementEdge(
        a="LEAD1", b="LEAD2", corr=0.72, n_sessions=250,
        ci_low=0.65, ci_high=0.78, flag=None,
    )

    upsert_comovement(db, [edge], as_of=AS_OF)
    result = movers_with(db, "LEAD1", as_of=AS_OF)

    assert len(result) == 1, f"expected exactly one edge, got {result!r}"
    got = result[0]
    assert {got.a, got.b} == {"LEAD1", "LEAD2"}
    assert got.corr == pytest.approx(0.72)
    assert got.n_sessions == 250
    assert got.ci_low == pytest.approx(0.65)
    assert got.ci_high == pytest.approx(0.78)
    assert got.flag is None


def test_upserting_the_same_edges_twice_is_idempotent(db):
    """The D-97 property: re-running `upsert_comovement` with the identical
    edge and `as_of` must not grow the collection or change the stored
    numbers -- the loader-drop bug reborn as a writer that inserts a fresh
    duplicate document on every re-run instead of replacing the one it
    already wrote.

    Falsifies if the second call leaves `moves_with.count()` at 2 instead of
    1 (duplicated rather than replaced), or if the reread values differ from
    the first read (e.g. a non-deterministic key made the second write land
    on a different document, leaving the original stale under some other
    read path).
    """
    edge = ComovementEdge(
        a="LEAD1", b="LEAD2", corr=0.55, n_sessions=250,
        ci_low=0.40, ci_high=0.65, flag="duplicate_series",
    )

    upsert_comovement(db, [edge], as_of=AS_OF)
    count_after_first = db.collection(EDGE).count()
    first_read = movers_with(db, "LEAD1", as_of=AS_OF)

    upsert_comovement(db, [edge], as_of=AS_OF)
    count_after_second = db.collection(EDGE).count()
    second_read = movers_with(db, "LEAD1", as_of=AS_OF)

    assert count_after_first == 1, "expected one document after the first upsert"
    assert count_after_second == count_after_first, (
        "a second upsert of the same edge must replace, not duplicate")
    assert len(second_read) == 1
    assert second_read[0].corr == pytest.approx(first_read[0].corr)
    assert second_read[0].ci_low == pytest.approx(first_read[0].ci_low)
    assert second_read[0].ci_high == pytest.approx(first_read[0].ci_high)
    assert second_read[0].flag == first_read[0].flag


def test_point_in_time_excludes_an_edge_measured_on_a_later_date(db):
    """Two edges both involving LEAD1, measured `as_of` two different dates.
    Reading `as_of` the earlier date must surface only the edge measured
    then -- never the one measured later, even though it shares the queried
    symbol.

    Made genuinely disconfirming per D-82 (a real lookahead bug that shipped
    past 80 tests): the assertion is not "the result is empty", which a
    query that filters on the wrong field would also satisfy vacuously, but
    "the result contains the near edge and specifically excludes the far
    one".
    """
    near = ComovementEdge(a="LEAD1", b="NEAR", corr=0.6, n_sessions=250,
                           ci_low=0.5, ci_high=0.7, flag=None)
    far = ComovementEdge(a="LEAD1", b="FAR", corr=0.6, n_sessions=250,
                          ci_low=0.5, ci_high=0.7, flag=None)
    upsert_comovement(db, [near], as_of=EARLY)
    upsert_comovement(db, [far], as_of=LATE)

    result = movers_with(db, "LEAD1", as_of=EARLY)

    partners = {e.a if e.b == "LEAD1" else e.b for e in result}
    assert partners == {"NEAR"}, (
        f"expected only the edge measured as_of={EARLY}, got partners {partners}")


def test_movers_with_finds_a_symbol_stored_as_either_side_of_the_edge(db):
    """Co-movement is symmetric (D-95); only one direction is stored per
    pair. `movers_with("LEAD1", ...)` must find LEAD1 whether it was written
    as the edge's `a` (LEAD1->NEIGH1) or its `b` (NEIGH2->LEAD1).

    Falsifies if the read query only matches on `_from` (or only `_to`),
    which would silently drop half of a symbol's edges depending on which
    order `comovement_edges()` happened to emit them in.
    """
    as_a = ComovementEdge(a="LEAD1", b="NEIGH1", corr=0.5, n_sessions=250,
                           ci_low=0.4, ci_high=0.6, flag=None)
    as_b = ComovementEdge(a="NEIGH2", b="LEAD1", corr=0.5, n_sessions=250,
                           ci_low=0.4, ci_high=0.6, flag=None)
    upsert_comovement(db, [as_a, as_b], as_of=AS_OF)

    result = movers_with(db, "LEAD1", as_of=AS_OF)

    partners = {e.a if e.b == "LEAD1" else e.b for e in result}
    assert partners == {"NEIGH1", "NEIGH2"}


def test_min_abs_corr_filters_on_absolute_value_so_negative_edges_survive(db):
    """A strongly negative edge (corr=-0.8) must survive a `min_abs_corr=0.5`
    filter, while a weak positive one (corr=0.3) must not.

    Falsifies if the filter compares `corr >= min_abs_corr` directly instead
    of `abs(corr) >= min_abs_corr` -- which would drop every negative edge
    regardless of strength, silently discarding half of D-95's calibrated
    relationships.
    """
    strong_negative = ComovementEdge(a="LEAD1", b="INVERSE", corr=-0.8,
                                      n_sessions=250, ci_low=-0.85, ci_high=-0.72,
                                      flag=None)
    weak_positive = ComovementEdge(a="LEAD1", b="WEAK", corr=0.3,
                                    n_sessions=250, ci_low=0.18, ci_high=0.41,
                                    flag=None)
    upsert_comovement(db, [strong_negative, weak_positive], as_of=AS_OF)

    result = movers_with(db, "LEAD1", as_of=AS_OF, min_abs_corr=0.5)

    assert len(result) == 1, f"expected only the strong negative edge, got {result!r}"
    assert result[0].corr == pytest.approx(-0.8)


def test_movers_with_unknown_symbol_returns_empty_not_an_error(db):
    """A symbol with no stored edges at all must come back as `[]`, not
    raise -- mirroring `ArangoTopology.laggers_of`'s same contract.

    Falsifies if this raises (e.g. an AQL/document-not-found error) instead
    of returning an empty list. Seeds one unrelated edge first, so an empty
    result here is a genuine "nothing found for this symbol", not "the
    collection itself is empty" (never assert on an empty result set without
    first proving the setup produced something).
    """
    seeded = ComovementEdge(a="LEAD1", b="LEAD2", corr=0.7, n_sessions=250,
                             ci_low=0.6, ci_high=0.8, flag=None)
    upsert_comovement(db, [seeded], as_of=AS_OF)
    assert db.collection(EDGE).count() == 1, "setup did not seed an edge"

    result = movers_with(db, "NONEXISTENT", as_of=AS_OF)

    assert result == []


# Point-in-time as "the most recent snapshot at or before as_of" (matching
# ArangoTopology.laggers_of's `<=` convention, D-16), not "the snapshot dated
# exactly as_of". Dates below are distinct from AS_OF/EARLY/LATE above so
# these tests don't accidentally share fixtures with the equality-era ones.
GAP_EARLY = date(2026, 1, 5)
GAP_QUERY = date(2026, 1, 7)
GAP_LATE = date(2026, 1, 9)


def test_movers_with_falls_back_to_the_latest_snapshot_at_or_before_as_of(db):
    """A snapshot exists for GAP_EARLY and another for GAP_LATE, but none for
    the queried date in between. The query must resolve backwards to the
    nearest snapshot at or before it (GAP_EARLY), not come back empty just
    because that exact date was never ingested.

    Falsifies if: `movers_with(..., as_of=GAP_QUERY)` returns `[]` -- which is
    exactly what `FILTER e.as_of == @as_of` produces today, since no edge was
    ever written with `as_of == GAP_QUERY`.
    """
    near = ComovementEdge(a="LEAD1", b="NEARX", corr=0.6, n_sessions=250,
                           ci_low=0.5, ci_high=0.7, flag=None)
    far = ComovementEdge(a="LEAD1", b="FARX", corr=0.6, n_sessions=250,
                          ci_low=0.5, ci_high=0.7, flag=None)
    upsert_comovement(db, [near], as_of=GAP_EARLY)
    upsert_comovement(db, [far], as_of=GAP_LATE)

    result = movers_with(db, "LEAD1", as_of=GAP_QUERY)

    assert result != [], "expected the GAP_EARLY snapshot, got nothing"
    partners = {e.a if e.b == "LEAD1" else e.b for e in result}
    assert partners == {"NEARX"}, (
        f"expected only the snapshot at or before {GAP_QUERY}, got partners {partners}")


def test_movers_with_never_blends_two_snapshots_of_the_same_pair(db):
    """The same pair measured twice, at GAP_EARLY and GAP_LATE, with
    different `corr` each time. A query strictly between the two dates must
    resolve to exactly the GAP_EARLY snapshot's row -- one edge, carrying
    GAP_EARLY's `corr`, never GAP_LATE's value and never both rows.

    Falsifies if: the result has 2 edges for this pair (both snapshots
    returned), or 1 edge whose `corr` is 0.85 (the later, wrong measurement)
    instead of 0.55.
    """
    early_measurement = ComovementEdge(a="LEAD1", b="PARTNER", corr=0.55,
                                        n_sessions=250, ci_low=0.45, ci_high=0.65,
                                        flag=None)
    late_measurement = ComovementEdge(a="LEAD1", b="PARTNER", corr=0.85,
                                       n_sessions=250, ci_low=0.75, ci_high=0.90,
                                       flag=None)
    upsert_comovement(db, [early_measurement], as_of=GAP_EARLY)
    upsert_comovement(db, [late_measurement], as_of=GAP_LATE)

    result = movers_with(db, "LEAD1", as_of=GAP_QUERY)

    assert len(result) == 1, f"expected exactly one edge for the pair, got {result!r}"
    assert result[0].corr == pytest.approx(0.55), (
        f"expected the GAP_EARLY corr (0.55), got {result[0].corr}")


def test_movers_with_returns_empty_before_the_earliest_snapshot(db):
    """A snapshot exists only for GAP_LATE. Querying a date before it must
    still come back `[]` -- the backwards fallback must not reach into the
    future, and there is no earlier snapshot to fall back to either.

    Falsifies if: the "most recent as_of <= query" resolution is implemented
    wrong-way-round (e.g. nearest by absolute distance rather than <=) and
    ends up returning the GAP_LATE edge for a query dated before it exists.
    """
    late = ComovementEdge(a="LEAD1", b="FARX", corr=0.6, n_sessions=250,
                           ci_low=0.5, ci_high=0.7, flag=None)
    upsert_comovement(db, [late], as_of=GAP_LATE)

    result = movers_with(db, "LEAD1", as_of=GAP_EARLY)

    assert result == []


def test_movers_with_an_exact_snapshot_date_returns_that_snapshot_not_an_earlier_one(db):
    """Two snapshots exist, GAP_EARLY and GAP_LATE. Querying exactly
    GAP_LATE -- a date that has its own snapshot -- must return GAP_LATE's
    edge, not GAP_EARLY's (which is also `<= as_of` and so must not be
    picked instead), and must not blend the two.

    Falsifies if: the fallback logic ignores an exact match and always walks
    back to the earliest available snapshot, or returns both snapshots' rows
    for a symbol that appears in each.
    """
    early = ComovementEdge(a="LEAD1", b="NEARX", corr=0.6, n_sessions=250,
                            ci_low=0.5, ci_high=0.7, flag=None)
    late = ComovementEdge(a="LEAD1", b="FARX", corr=0.6, n_sessions=250,
                           ci_low=0.5, ci_high=0.7, flag=None)
    upsert_comovement(db, [early], as_of=GAP_EARLY)
    upsert_comovement(db, [late], as_of=GAP_LATE)

    result = movers_with(db, "LEAD1", as_of=GAP_LATE)

    assert result != [], "expected the GAP_LATE snapshot, got nothing"
    partners = {e.a if e.b == "LEAD1" else e.b for e in result}
    assert partners == {"FARX"}, (
        f"expected only the exact-date snapshot, got partners {partners}")
