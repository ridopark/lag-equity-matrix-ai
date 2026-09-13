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
import re
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

# ArangoDB's legal `_key` charset (documented key-generators.html): letters,
# digits, and `_ - : . @ ( ) + , = ; $ ! * ' %`. Anything else -- including
# today's `>` (supply_edge_docs) and `~` (comention_edge_docs) -- is rejected
# by the server with `[HTTP 400][ERR 1221] illegal document key` (Q-64).
_ARANGO_LEGAL_KEY = re.compile(r"^[A-Za-z0-9_\-:.@()+,=;$!*'%]+$")


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
    """Two calls on the same input must produce byte-identical `_key` lists,
    and the two distinct (supplier, customer) rows must not collide.

    Does not pin a specific separator or format (Q-64 in
    docs/spikes/overall.md: `supplier->customer` uses an illegal `>`, and the
    corrected key must also carry `filing_date` -- see
    `test_supply_edge_docs_preserves_distinct_filing_dates` -- so asserting a
    literal key string here would pin the very scheme Q-64 disproved).
    Falsifies if `_key` is omitted (today's behaviour -- ArangoDB would then
    assign a fresh, non-reproducible key each call), derived from anything
    that varies between calls (e.g. an incrementing counter, a timestamp), or
    collides between the two distinct rows below."""
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
    assert len(set(keys_first)) == len(keys_first), "distinct rows must not collide on _key"


def test_supply_edge_docs_preserves_distinct_filing_dates():
    """Q-64 correction: a supplier/customer pair re-filed at a LATER date
    (e.g. a restated filing) must NOT collapse into one document -- the live
    database's `supplies_to` collection holds up to 15 dated copies of a
    single (supplier, customer) pair (e.g. QRVO->AAPL across 8 distinct
    filing dates spanning 2019-2026) precisely so that
    `ArangoTopology.laggers_of(..., as_of=...)`'s point-in-time filter
    (D-16, D-72) has per-date history to walk. Collapsing on the pair alone
    would delete that history, which is worse than the illegal-key bug it
    would otherwise fix.

    Supersedes the old `test_supply_edge_docs_dedupes_a_repeated_pair`, whose
    name and assertions (`len(docs) == 1`) pinned exactly this data loss as
    the intended behaviour -- as did `supply_edge_docs`'s own docstring
    ("collapse to one document, keyed on the pair"), which described a
    data-destroying design, not a spec to preserve. D-97 requires a *stable*
    key so a re-run upserts instead of duplicating -- it never required a
    *pair-only* key; `filing_date` can be part of that stable key too.

    Falsifies if the function still keys on (supplier, customer) alone and
    drops all but one row for a repeated pair -- today's code and docstring
    do exactly that."""
    rows = _supply_rows([
        {"supplier": "SUPA", "customer": "CUSTA", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "first filing", "counterparty": "Cust A Inc."},
        {"supplier": "SUPA", "customer": "CUSTA", "filing_date": "2024-06-01",
         "pct": "15.0", "passage": "restated filing", "counterparty": "Cust A Inc."},
    ])

    docs = supply_edge_docs(rows)

    assert len(docs) == 2
    assert len({d["_key"] for d in docs}) == 2
    assert {d["filing_date"] for d in docs} == {"2024-01-01", "2024-06-01"}


def test_supply_edge_docs_collapses_an_exact_duplicate_row():
    """The other half of the distinction above: two rows for the same
    (supplier, customer) pair AND the same `filing_date` -- e.g. the loader
    re-reads an unchanged filing on a second run -- are the same fact
    restated, not new history, and must still collapse to one document.
    This is the half of D-97's idempotency guarantee that survives the Q-64
    correction: same filing -> one doc (idempotent, here); different filing
    dates -> separate docs, never collapsed
    (`test_supply_edge_docs_preserves_distinct_filing_dates`). Together the
    two tests are the whole contract -- neither alone is sufficient.

    The live round-trip test (`test_edge_keys_round_trip_into_a_live_arango_
    database`) cannot catch a regression here: if a future fix emitted one
    doc per input row instead of deduping, two identical rows would produce
    two docs carrying the *same* `_key`, and inserting both into ArangoDB
    would still collapse them via `overwrite_mode="update"` -- the live
    collection count would stay 1 even though the in-memory contract (dedupe
    happens in `supply_edge_docs` itself, before any I/O) was broken. Only a
    pure assertion on `len(docs)` pins that.

    Falsifies if a re-run of the loader would insert a second copy of a
    filing that has not changed -- i.e. if the fix that makes
    `test_supply_edge_docs_preserves_distinct_filing_dates` pass does so by
    keying on something that varies even when the filing itself does not
    (e.g. row position/order), which would make each loader re-run grow
    `supplies_to` without bound -- the original D-97 failure mode."""
    rows = _supply_rows([
        {"supplier": "SUPA", "customer": "CUSTA", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "first read", "counterparty": "Cust A Inc."},
        {"supplier": "SUPA", "customer": "CUSTA", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "second read, same filing", "counterparty": "Cust A Inc."},
    ])

    docs = supply_edge_docs(rows)

    assert len(docs) == 1
    assert docs[0]["filing_date"] == "2024-01-01"


def test_comention_edge_docs_have_deterministic_keys():
    """Mirrors the supply-edge test for co-mention edges. Unlike
    `supplies_to`, `co_mentioned` has no per-row date dimension -- the live
    database's 2,129 edges are 2,129 distinct pairs -- so a pair-only key
    stays correct; only the separator must change (Q-64: `~` is illegal, see
    `test_comention_edge_key_is_a_legal_arango_key`). Does not pin a literal
    key string here for the same reason as the supply-edge test above.
    Falsifies if `_key` is absent, non-reproducible, or built from a sorted
    pair (which would silently rename the key for any row where the upstream
    query emitted b before a)."""
    rows = _comention_rows([
        {"a": "AAA", "b": "BBB", "n": "30", "pmi": "1.2345",
         "first_seen": "2024-01-01", "last_seen": "2024-03-01"},
    ])

    keys_first = [d["_key"] for d in comention_edge_docs(rows)]
    keys_second = [d["_key"] for d in comention_edge_docs(rows)]

    assert keys_first, "expected at least one doc, got an empty list"
    assert keys_first == keys_second


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


def test_supply_edge_key_is_a_legal_arango_key():
    """Q-64: `supply_edge_docs`' `_key` must contain no character outside
    ArangoDB's legal key charset, whichever legal separator ends up chosen --
    this asserts against the charset itself, not against `->` specifically,
    so the test survives the fix rather than dictating it.

    Falsifies today: the key is `SUPA->CUSTA`, and `>` is not in the legal
    set -- the server returns `[HTTP 400][ERR 1221] illegal document key`."""
    rows = _supply_rows([
        {"supplier": "AAA", "customer": "BBB", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "text", "counterparty": "BBB Inc."},
    ])

    key = supply_edge_docs(rows)[0]["_key"]

    assert _ARANGO_LEGAL_KEY.match(key), f"{key!r} contains an illegal ArangoDB key character"


def test_comention_edge_key_is_a_legal_arango_key():
    """Mirrors the above for `comention_edge_docs`.

    Falsifies today: the key is `AAA~BBB`, and `~` is not in the legal set --
    the same `[HTTP 400][ERR 1221] illegal document key` rejection."""
    rows = _comention_rows([
        {"a": "AAA", "b": "BBB", "n": "30", "pmi": "1.2345",
         "first_seen": "2024-01-01", "last_seen": "2024-03-01"},
    ])

    key = comention_edge_docs(rows)[0]["_key"]

    assert _ARANGO_LEGAL_KEY.match(key), f"{key!r} contains an illegal ArangoDB key character"


def test_supply_edge_key_still_distinguishes_direction():
    """`supplies_to` is directed (D-73: `_from`=supplier, `_to`=customer), so
    the key for (supplier=AAA, customer=BBB) must differ from the key for the
    reversed pair (supplier=BBB, customer=AAA) -- two distinct real
    relationships must not collapse onto one document.

    Falsifies if the legal-charset fix is done by sorting the pair or by
    dropping the separator entirely, either of which would make both
    directions key identically and silently merge them on upsert."""
    forward = _supply_rows([
        {"supplier": "AAA", "customer": "BBB", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "text", "counterparty": "BBB Inc."},
    ])
    reverse = _supply_rows([
        {"supplier": "BBB", "customer": "AAA", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "text", "counterparty": "AAA Inc."},
    ])

    forward_key = supply_edge_docs(forward)[0]["_key"]
    reverse_key = supply_edge_docs(reverse)[0]["_key"]

    assert forward_key != reverse_key


def test_edge_keys_round_trip_into_a_live_arango_database():
    """The test whose absence hid Q-64: inserts the docs both loaders' edge
    functions actually produce into a real, disposable ArangoDB edge
    collection, then re-inserts the same docs a second time under
    `overwrite_mode="update"` -- proving real idempotency, not just that the
    returned dicts have the right shape (which is all the tests above this
    one, and the pre-existing shape tests, ever checked).

    Falsifies if: the first insert raises (today's behaviour -- `>`/`~` keys
    are rejected with `[HTTP 400][ERR 1221] illegal document key`), or the
    collection count after the second insert differs from after the first
    (would mean the key did not survive as a stable upsert target and the
    second insert duplicated rather than updated)."""
    db = arango_db_or_skip(ARANGO_DB_NAME)
    if not db.has_collection("q64_edges"):
        db.create_collection("q64_edges", edge=True)
    edges = db.collection("q64_edges")
    edges.truncate()

    supply_rows = _supply_rows([
        {"supplier": "AAA", "customer": "BBB", "filing_date": "2024-01-01",
         "pct": "12.5", "passage": "text", "counterparty": "BBB Inc."},
    ])
    comention_rows = _comention_rows([
        {"a": "AAA", "b": "BBB", "n": "30", "pmi": "1.2345",
         "first_seen": "2024-01-01", "last_seen": "2024-03-01"},
    ])
    docs = supply_edge_docs(supply_rows) + comention_edge_docs(comention_rows)

    for doc in docs:
        edges.insert(doc, overwrite_mode="update")
    assert edges.count() == len(docs)

    for doc in docs:
        edges.insert(doc, overwrite_mode="update")
    assert edges.count() == len(docs)
