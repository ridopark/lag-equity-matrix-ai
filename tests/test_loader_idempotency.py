"""RED for D-97: the loaders must upsert, never `_drop`.

`load_vectors.py:84` runs `if (db._collection("article")) { db._drop("article"); }`
and `load_arango.py:89` does the same for `equity`/`supplies_to`/`co_mentioned`
-- a full rebuild, not an update. `load_arango.py:85` already carries the
comment recording that this exact pattern destroyed 47,640 embeddings and
their vector index once. Scheduling these scripts daily (this plan's whole
point) would convert that one-off accident into a nightly one, so PHASE-1
exists to make the drop structurally impossible before anything schedules it.

Two properties, each pinned by its own tests below:

1. **Never drops.** The JS text shipped to `arangosh` must not contain
   `_drop` at all. This is a stronger guard than grepping the *script* for
   the string post-hoc (the plan's own completion criterion does that too),
   because it fails loudly the moment a future edit reintroduces the pattern
   inside the generated string, not just inside the source file.
2. **Deterministic keys.** Today's `bulk()` calls insert docs with no `_key`,
   so ArangoDB assigns one and a second run of the script inserts a second,
   distinct edge for the same relationship rather than updating the first.
   `supply_edge_docs`/`comention_edge_docs` must derive `_key` from the
   relationship's own identity (`supplier->customer`, `a~b`) so that
   `overwriteMode:"replace"` upserts on a re-run instead of duplicating.

Transport (`ssh -> kubectl exec -> arangosh`) is not exercisable from here
(Q-43's same accepted gap) -- so these tests hit the pure pieces directly:
the JS-generating functions (return a string, no I/O) and the document-shaping
functions (return dicts, no I/O). One live-gated test at the bottom proves
the upsert *strategy* (deterministic key + `overwrite_mode="update"`) against
a real, throwaway ArangoDB database; it does not and cannot exercise the SSH
transport itself.

`ensure_collections_js`, `supply_edge_docs`, `comention_edge_docs`
(`scripts/load_arango.py`) and `ensure_article_js` (`scripts/load_vectors.py`)
do not exist yet -- only inline blocks inside each script's `main()` do -- so
importing them below fails on collection with `ImportError`. That failure is
the RED this file exists to produce.
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd

from conftest import arango_db_or_skip

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

# Not yet extracted from scripts/load_arango.py and scripts/load_vectors.py
# main() -- this import is expected to raise ImportError until TASK-1.2 lands.
from load_arango import comention_edge_docs, ensure_collections_js, supply_edge_docs  # noqa: E402
from load_vectors import ensure_article  # noqa: E402

ARANGO_DB_NAME = "test_loader_idempotency"


def _supply_rows(rows: list[dict]) -> list:
    """`load_arango.py`'s `sup.itertuples()` shape: supplier, customer,
    filing_date, pct, passage, counterparty -- exactly the columns `main()`
    already assigns to `sup.columns` before iterating."""
    df = pd.DataFrame(rows, columns=[
        "supplier", "customer", "filing_date", "pct", "passage", "counterparty"])
    return list(df.itertuples())


def _comention_rows(rows: list[dict]) -> list:
    """`load_arango.py`'s `com.itertuples()` shape: a, b, n, pmi, first_seen,
    last_seen."""
    df = pd.DataFrame(rows, columns=["a", "b", "n", "pmi", "first_seen", "last_seen"])
    return list(df.itertuples())


def test_ensure_collections_js_never_drops():
    """Falsifies if the generated JS contains `_drop` anywhere -- the exact
    substring `db._drop(name)` uses today."""
    js = ensure_collections_js({"equity": 2, "supplies_to": 3, "co_mentioned": 3})
    assert "_drop" not in js


class _FakeArangoDb:
    """Records every collection operation; refuses none."""

    def __init__(self, existing: set[str]):
        self.existing = existing
        self.calls: list[tuple[str, str]] = []

    def has_collection(self, name):
        self.calls.append(("has_collection", name))
        return name in self.existing

    def create_collection(self, name):
        self.calls.append(("create_collection", name))
        self.existing.add(name)

    def delete_collection(self, name):
        self.calls.append(("delete_collection", name))
        self.existing.discard(name)


def test_ensure_article_never_drops_an_existing_collection():
    """The D-97 guarantee, now asserted on behaviour rather than on the text of
    a generated script: `ensure_article` must never delete `article`.

    This replaces a substring check (`"_drop" not in js`) that could only
    inspect arangosh source. `load_vectors.py` now speaks to ArangoDB through
    python-arango, so there is no script to grep -- and asserting on the calls
    made is a stronger claim than asserting on the text that would have made
    them.

    Falsifies if `ensure_article` calls `delete_collection`, or re-creates a
    collection that already exists. Either would destroy the corpus; the code
    this replaced did exactly that once, costing 47,640 embeddings.
    """
    db = _FakeArangoDb({"article"})
    ensure_article(db)
    assert ("delete_collection", "article") not in db.calls
    assert ("create_collection", "article") not in db.calls


def test_ensure_article_creates_the_collection_when_absent():
    """The other half: a genuinely empty database must get the collection.

    Falsifies if `ensure_article` is so cautious it never creates anything,
    which would pass the test above while making a first run impossible.
    """
    db = _FakeArangoDb(set())
    ensure_article(db)
    assert ("create_collection", "article") in db.calls
    assert ("delete_collection", "article") not in db.calls


def test_supply_edge_docs_have_deterministic_keys():
    """Two calls on the same input must produce byte-identical `_key` lists.
    Falsifies if `_key` is omitted (today's behaviour -- ArangoDB would then
    assign a fresh, non-reproducible key each call) or derived from anything
    that varies between calls (e.g. an incrementing counter, a timestamp)."""
    rows = _supply_rows([
        {"supplier": "SUPA", "customer": "CUSTA", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "text a", "counterparty": "Cust A Inc."},
        {"supplier": "SUPB", "customer": "CUSTB", "filing_date": "2024-02-01",
         "pct": "", "passage": "text b", "counterparty": "Cust B Inc."},
    ])

    keys_first = [d["_key"] for d in supply_edge_docs(rows)]
    keys_second = [d["_key"] for d in supply_edge_docs(rows)]

    assert keys_first, "expected at least one doc, got an empty list"
    assert keys_first == keys_second
    assert keys_first == ["SUPA->CUSTA", "SUPB->CUSTB"]


def test_supply_edge_docs_dedupes_a_repeated_pair():
    """Two filings restating the same (supplier, customer) relationship must
    collapse to one document keyed `supplier->customer`. Falsifies if the
    function emits one doc per input row regardless of key collisions --
    today's `bulk()` inline list comprehension does exactly that, which is
    what makes a second script run double the graph instead of updating it."""
    rows = _supply_rows([
        {"supplier": "SUPA", "customer": "CUSTA", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "first filing", "counterparty": "Cust A Inc."},
        {"supplier": "SUPA", "customer": "CUSTA", "filing_date": "2024-06-01",
         "pct": "15.0", "passage": "restated filing", "counterparty": "Cust A Inc."},
    ])

    docs = supply_edge_docs(rows)

    assert len(docs) == 1
    assert docs[0]["_key"] == "SUPA->CUSTA"


def test_comention_edge_docs_have_deterministic_keys():
    """Mirrors the supply-edge test for co-mention edges, keyed `a~b` in the
    row's own order -- not re-sorted, since the upstream query already fixes
    an order per row. Falsifies if `_key` is absent, non-reproducible, or
    built from a sorted pair (which would silently rename the key for any
    row where the upstream query emitted b before a)."""
    rows = _comention_rows([
        {"a": "AAA", "b": "BBB", "n": "30", "pmi": "1.2345",
         "first_seen": "2024-01-01", "last_seen": "2024-03-01"},
    ])

    keys_first = [d["_key"] for d in comention_edge_docs(rows)]
    keys_second = [d["_key"] for d in comention_edge_docs(rows)]

    assert keys_first, "expected at least one doc, got an empty list"
    assert keys_first == keys_second
    assert keys_first == ["AAA~BBB"]


def test_upsert_strategy_is_idempotent_against_a_real_database():
    """Proves the upsert *strategy* (deterministic key + `overwrite_mode=
    "update"`) against a real, throwaway ArangoDB database -- not the SSH
    transport `load_arango.py`/`load_vectors.py` actually use to reach it,
    which is out of reach from here (Q-43's accepted gap).

    Seeds one pre-existing, unrelated document, then inserts a second document
    twice under the same `_key`. Falsifies if either (a) the seeded document
    is missing or changed afterwards (would mean something dropped/touched
    unrelated data), or (b) the collection count is 3 instead of 2 after the
    second insert (would mean the second insert duplicated rather than
    updated the document)."""
    db = arango_db_or_skip(ARANGO_DB_NAME)
    if not db.has_collection("equity"):
        db.create_collection("equity")
    equity = db.collection("equity")
    equity.truncate()

    seed = {"_key": "SEED", "symbol": "SEED", "note": "pre-existing, unrelated"}
    equity.insert(seed)

    equity.insert({"_key": "AAPL", "symbol": "AAPL", "note": "v1"},
                  overwrite_mode="update")
    equity.insert({"_key": "AAPL", "symbol": "AAPL", "note": "v2"},
                  overwrite_mode="update")

    reread_seed = equity.get("SEED")
    assert reread_seed["symbol"] == "SEED"
    assert reread_seed["note"] == "pre-existing, unrelated"
    assert equity.count() == 2

    reread_aapl = equity.get("AAPL")
    assert reread_aapl["note"] == "v2"
