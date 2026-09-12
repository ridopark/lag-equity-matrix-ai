"""PHASE-4 (PLAN-2026-09-12-relatedness-and-sectors): `ArangoTopology.sic_of`
/ `.comention_pmi` -- the two read-only relatedness lookups REQ-2 adds, so
PHASE-5 can wire sector-match and co-mention-PMI into `QuantPerspective`.

Mirrors `test_arango_topology.py`'s fixture style exactly: its own disposable
`test_`-prefixed database (never the real `lagmatrix`, whose `equity`/
`co_mentioned` collections this file must never touch -- a prior incident in
this repo destroyed 47,640 embeddings via a same-database write), fictional
symbols (real `supplies_to`/`co_mentioned` edges are mid-audit per D-78 and
are not a safe stand-in even read-only), and D-38's "skip, don't pass
vacuously" live-gating via `arango_db_or_skip`.

`sic_of` reads plain `sic` fields off `equity` vertices (no traversal, no
`as_of` -- SIC has no point-in-time dimension per this plan's own "Point-in-
time correctness" section). `comention_pmi` reads `co_mentioned` edges
incident to one symbol in either direction -- unlike `supplies_to`,
`co_mentioned` edges are not directional (`load_arango.py`'s
`comention_edge_docs` keys them `a~b` from an unordered pair), so a caller
asking "who is SYMB co-mentioned with" must find edges where SYMB is on
either side, keyed by the *other* symbol.
"""

from __future__ import annotations

import pytest

from conftest import arango_db_or_skip
from lagmatrix.adapters.arango import ArangoTopology

ARANGO_DB_NAME = "test_arango_relatedness"
VERTEX = "equity"
EDGE = "co_mentioned"


@pytest.fixture(scope="module")
def topology():
    """Connect to a real ArangoDB and seed a throwaway fixture graph.

    Skips (not fails, not passes vacuously) if nothing is reachable at
    `LAGMATRIX_ARANGO_URL` -- true on this dev machine by default (`.env`'s
    `LAGMATRIX_ARANGO_URL=http://localhost:8529` has nothing listening; the
    live port-forward is 18529), same as `test_arango_topology.py`.
    """
    db = arango_db_or_skip(ARANGO_DB_NAME)

    if not db.has_collection(VERTEX):
        db.create_collection(VERTEX)
    if not db.has_collection(EDGE):
        db.create_collection(EDGE, edge=True)
    vertices = db.collection(VERTEX)
    edges = db.collection(EDGE)
    vertices.truncate()
    edges.truncate()

    # SYMA/SYMB share a SIC; SYMC exists but has never resolved one (the
    # ~0.1% case REQ-1/TASK-3.2 excludes rather than writing as null).
    # SYMD is not inserted at all -- not even an unresolved vertex.
    vertices.insert({"_key": "SYMA", "symbol": "SYMA", "sic": "3674"})
    vertices.insert({"_key": "SYMB", "symbol": "SYMB", "sic": "3674"})
    vertices.insert({"_key": "SYMC", "symbol": "SYMC"})
    # A vertex with no co-mention edges at all, for the empty-result test.
    vertices.insert({"_key": "SYMA_LONER", "symbol": "SYMA_LONER"})

    # No explicit `_key`: ArangoDB rejects `~` in document keys (illegal
    # document key), so `load_arango.py`'s own `f"{a}~{b}"` scheme -- used
    # only in `comention_edge_docs`'s pure-shape test, never against a real
    # database in this repo's test suite -- cannot be mirrored literally
    # here. Letting Arango assign the key is fine: `comention_pmi` looks
    # edges up by `_from`/`_to`, never by `_key`.
    edges.insert({
        "_from": f"{VERTEX}/SYMA", "_to": f"{VERTEX}/SYMB",
        "pmi": 1.2, "relation": "co_mentioned",
    })
    edges.insert({
        "_from": f"{VERTEX}/SYMB", "_to": f"{VERTEX}/SYMC",
        "pmi": 0.4, "relation": "co_mentioned",
    })

    return ArangoTopology(db)


def test_sic_of_returns_resolved_symbols_only(topology):
    """`sic_of` must map every symbol whose vertex carries a `sic` field, and
    must simply omit -- not map to `None` -- both a vertex that exists but
    never resolved one (SYMC) and a symbol that is not a vertex at all
    (SYMD), so every caller has exactly one "unknown" representation.

    Falsifies if: SYMC or SYMD appear as keys (mapped to `None` or anything
    else), or SYMA's/SYMB's value comes back wrong.
    """
    result = topology.sic_of(["SYMA", "SYMB", "SYMC", "SYMD"])

    assert result == {"SYMA": "3674", "SYMB": "3674"}


def test_comention_pmi_finds_edges_in_either_direction(topology):
    """SYMB sits on the `_from` side of one seeded edge (SYMA~SYMB) and the
    `_to` side of the other (SYMB~SYMC). Both must be found from one query,
    each keyed by the *other* symbol, not SYMB itself.

    Falsifies if: only one direction is found (an AQL filter checking
    `_from` alone would drop SYMA~SYMB), or the key/value are swapped (e.g.
    `{"SYMA": 1.2}` keyed on the query symbol instead of the neighbour).
    """
    result = topology.comention_pmi("SYMB")

    assert result == {"SYMA": 1.2, "SYMC": 0.4}


def test_comention_pmi_empty_for_a_symbol_with_no_edges(topology):
    """A vertex that exists but has no incident `co_mentioned` edge must come
    back as `{}`, not an error and not a dict containing the symbol itself.

    Falsifies if: this raises, or the result is non-empty.
    """
    result = topology.comention_pmi("SYMA_LONER")

    assert result == {}


def test_comention_pmi_empty_when_collection_absent():
    """Against a *fresh* disposable database with no `co_mentioned` collection
    at all (a database that has never run `load_arango.py`), `comention_pmi`
    must degrade to `{}` -- mirroring `movers_with`'s existing `if not
    db.has_collection(...): return []` guard.

    Uses its own fixture (not the module-scoped `topology` above, which
    already created `co_mentioned`) so this test genuinely exercises a
    database where the collection has never existed.

    Falsifies if: this raises `CollectionNotFoundError` instead of
    degrading, which would take down `compute_quant_perspective` on any
    fresh database that hasn't run `load_arango.py` yet.
    """
    db = arango_db_or_skip("test_arango_relatedness_nocoll")
    if not db.has_collection(VERTEX):
        db.create_collection(VERTEX)
    assert not db.has_collection(EDGE)
    topology = ArangoTopology(db)

    result = topology.comention_pmi("ANY")

    assert result == {}
