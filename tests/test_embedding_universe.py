"""RED: the embedding universe must be derived from the graph, not from
`data/fires.csv` alone.

`scripts/load_vectors.py:92` scopes its whole candidate query to
`sorted(pd.read_csv("data/fires.csv").ticker.unique())` -- the 24-symbol
alert set. But the page's default source is scan-first
(`serve_index.html:629`), and neither scan mode nor `leader:` mode reads
`fires.csv` at all: `serve.py:160` routes them to `MarketScan` (over
`supplies_to`) and `CoMovementFollowers` (over `moves_with`). Measured
consequence: 58% of `leader:`-mode candidates and 48% of scan candidates
return news below the retrieval threshold, even though probing Alpaca
directly for 26 of them found news for all 26 (median 75 articles/symbol) --
it exists and was never requested, because the corpus was never scoped to
include it.

`embedding_universe(db, alert_symbols)` does not exist yet in
`lagmatrix.adapters.arango` -- only `ArangoTopology`, `upsert_comovement`,
and `movers_with` live there today. The import below is expected to raise
`ImportError`; that is the RED this file exists to produce.

Design this pins down (not yet decided in source):

- The universe is the distinct endpoints of BOTH edge collections
  (`supplies_to`'s supplier/customer pairs, `moves_with`'s co-movement
  pairs), read from both `_from` and `_to` on each, UNIONED with the caller's
  `alert_symbols` -- never replacing it. That union is the guard that
  matters: today's fires.csv-only behaviour must remain a strict subset of
  tomorrow's, so widening the corpus can never silently shrink it.
- Tier 2 of this work (fetching news for the `moves_with` followers) is
  purely a fetch over whatever this function returns -- deriving the
  universe from the graph, rather than hardcoding a second ticker list, is
  what lets that tier land with no further code change here. A symbol with
  no articles yet simply contributes none to the corpus.
- Returned as a sorted, de-duplicated list of plain symbols (`equity/AAPL`'s
  `_key`, "AAPL" -- not the vertex id), matching `plan_embeddings`'s own key
  convention.
- An absent OR present-but-empty edge collection degrades to the alert set
  alone rather than raising -- a fresh database legitimately has neither
  `supplies_to` nor `moves_with` yet.

Driven against a fake db exposing only the AQL surface `embedding_universe`
needs (`has_collection`, and `aql.execute` keyed on the same `@@collection`
bind-var convention `ArangoTopology`/`movers_with` already use elsewhere in
this module) -- no live ArangoDB required. One live-gated test at the bottom
proves the same contract against a real, throwaway database, and here it
runs rather than skips: an ssh tunnel holds `localhost:19999` open to the
homelab, which is `conftest.ARANGO_URL`'s default. It skips only where no
tunnel exists.

Never touches the real `lagmatrix` database: the fake here has no
relationship to it at all, and the live-gated test uses
`conftest.arango_db_or_skip`'s throwaway-database convention, same as every
other live-gated Arango test in this suite.
"""

from __future__ import annotations

from conftest import arango_db_or_skip


class _FakeAql:
    """Only the AQL surface `embedding_universe` calls: `execute(query,
    bind_vars)`, resolving the `@@collection` bind var to a list of `[_from,
    _to]` pairs. Does not parse `query` at all -- the collection to read
    comes entirely from `bind_vars["@collection"]`, matching how
    `ArangoTopology`/`movers_with` already parameterise collection names in
    this module."""

    def __init__(self, collections: dict[str, list[dict]]):
        self._collections = collections

    def execute(self, query, bind_vars=None):
        name = bind_vars["@collection"]
        return [[e["_from"], e["_to"]] for e in self._collections[name]]


class _FakeGraphDb:
    """A name absent from `collections` means "collection does not exist"
    (`has_collection` -> False) -- matching a fresh database that has never
    had `supplies_to` or `moves_with` created."""

    def __init__(self, collections: dict[str, list[dict]]):
        self.aql = _FakeAql(collections)
        self._collections = collections

    def has_collection(self, name):
        return name in self._collections


def _edge(frm: str, to: str) -> dict:
    return {"_from": f"equity/{frm}", "_to": f"equity/{to}"}


def test_supplies_to_endpoints_are_included_from_both_from_and_to():
    """Falsifies if only `_to` (or only `_from`) is read off `supplies_to`
    edges -- one of AAPL/MSFT would then be missing from the result."""
    from lagmatrix.adapters.arango import embedding_universe

    db = _FakeGraphDb({"supplies_to": [_edge("AAPL", "MSFT")]})

    result = embedding_universe(db, alert_symbols=[])

    assert result == ["AAPL", "MSFT"]


def test_moves_with_endpoints_are_included_from_both_a_and_b():
    """Falsifies if only one side of a `moves_with` edge is read -- one of
    AMD/NVDA would then be missing from the result."""
    from lagmatrix.adapters.arango import embedding_universe

    db = _FakeGraphDb({"moves_with": [_edge("NVDA", "AMD")]})

    result = embedding_universe(db, alert_symbols=[])

    assert result == ["AMD", "NVDA"]


def test_alert_set_is_unioned_in_not_shadowed_by_the_graph():
    """The guard that matters: a symbol appearing ONLY in `alert_symbols`,
    with no edge of its own in either collection, must still come back --
    proving union, not replacement. A regression here would silently shrink
    a corpus that took hours to build (per the embedding backfill this
    plan's Tier 2 depends on).

    Falsifies if `ALERT1` is missing from the result -- which is what a
    version that took the graph endpoints ALONE (ignoring `alert_symbols`,
    or intersecting instead of unioning) would produce.
    """
    from lagmatrix.adapters.arango import embedding_universe

    db = _FakeGraphDb({"supplies_to": [_edge("GRAPH1", "GRAPH2")]})

    result = embedding_universe(db, alert_symbols=["ALERT1"])

    assert set(result) == {"ALERT1", "GRAPH1", "GRAPH2"}


def test_result_is_sorted_deduplicated_plain_symbols():
    """`ALPHA` appears in both `supplies_to` and `alert_symbols`; the vertex
    ids carry the `equity/` prefix. The result must have no prefix, no
    repeats, and be in sorted order.

    Falsifies if: the `equity/` prefix survives (would sort/compare wrong
    and fail direct string equality against plain tickers elsewhere in the
    pipeline), `ALPHA` appears twice (dedup missing), or the order is
    insertion order rather than sorted (e.g. `["ZETA", "ALPHA", "BETA"]`).
    """
    from lagmatrix.adapters.arango import embedding_universe

    db = _FakeGraphDb({
        "supplies_to": [_edge("ZETA", "ALPHA")],
        "moves_with": [_edge("ALPHA", "BETA")],
    })

    result = embedding_universe(db, alert_symbols=["BETA"])

    assert result == ["ALPHA", "BETA", "ZETA"]


def test_absent_edge_collections_degrade_to_the_alert_set_without_raising():
    """A fresh database has neither `supplies_to` nor `moves_with` yet --
    `has_collection` returns `False` for both. This must degrade to the
    alert set, not raise (e.g. from querying a collection that does not
    exist).

    Falsifies if this raises, or if the result is anything other than
    exactly the (sorted) alert set.
    """
    from lagmatrix.adapters.arango import embedding_universe

    db = _FakeGraphDb({})

    result = embedding_universe(db, alert_symbols=["ZETA", "ALPHA"])

    assert result == ["ALPHA", "ZETA"]


def test_present_but_empty_edge_collections_degrade_to_the_alert_set():
    """Both collections exist (`has_collection` -> True) but hold no edges
    yet -- distinct from the absent case above, since a naive implementation
    could pass one and fail the other (e.g. checking existence but then
    mishandling a genuinely empty cursor).

    Falsifies if this raises, or returns anything beyond the alert set.
    """
    from lagmatrix.adapters.arango import embedding_universe

    db = _FakeGraphDb({"supplies_to": [], "moves_with": []})

    result = embedding_universe(db, alert_symbols=["ZETA"])

    assert result == ["ZETA"]


def test_embedding_universe_against_a_real_throwaway_database():
    """The same contract, proven against a real ArangoDB rather than the
    fake above -- catches an AQL syntax error the fake's substitute
    `execute` could never surface (e.g. a malformed `@@collection` bind-var
    query).

    This test RUNS here, it does not skip: `conftest.ARANGO_URL` defaults to
    `http://localhost:19999`, and an ssh tunnel holds that port open to the
    homelab. Verified by re-running it under `LAGMATRIX_REQUIRE_LIVE=1`, which
    turns an unreachable instance into a failure rather than a skip -- it still
    passed, so the AQL below really was executed by ArangoDB. It skips only on
    a machine with no tunnel. Uses `arango_db_or_skip`'s throwaway-database
    convention, never `lagmatrix`.
    """
    from lagmatrix.adapters.arango import embedding_universe

    db = arango_db_or_skip("test_embedding_universe")
    for name in ("supplies_to", "moves_with"):
        if not db.has_collection(name):
            db.create_collection(name, edge=True)
        db.collection(name).truncate()
    db.collection("supplies_to").insert(_edge("SUPA", "CUSTA"))
    db.collection("moves_with").insert(_edge("LEAD1", "NEIGH1"))

    result = embedding_universe(db, alert_symbols=["ALERT1"])

    assert set(result) == {"SUPA", "CUSTA", "LEAD1", "NEIGH1", "ALERT1"}
