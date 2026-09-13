"""RED for PHASE-3 (Q-63): `scripts/load_sectors.py` must upsert `sic`/
`sic_desc` onto `equity` vertices without ever dropping a collection or
full-replacing an existing document.

`scripts/load_arango.py`'s `bulk()` helper (scripts/load_arango.py:77) writes
`db.{collection}.insert(docs, {overwriteMode:"replace"})`. A `"replace"`-mode
write sends only the fields present in the payload -- `{_key, symbol}` for
`equity` -- and ArangoDB drops every field not in that payload from the
existing document. If `load_sectors.py` reused `bulk()` unmodified, or wrote
its own helper with the same `overwriteMode`, a later re-run of
`load_arango.py` (or of `load_sectors.py` itself, on a document missing
`sic`) would silently strip `sic`/`sic_desc` back off `equity`. This is the
exact class of accident that already destroyed 47,640 embeddings once in
this repo (see `load_arango.py`'s `ensure_collections_js` docstring), so this
file pins `load_sectors.py`'s write path to `overwriteMode:"update"` (merge
semantics) and never `_drop`.

`sector_update_docs` and `upsert_js` (`scripts/load_sectors.py`) do not exist
yet -- only this plan's brief describes them -- so importing them below fails
on collection with `ImportError`. That failure is the RED this file exists
to produce.
"""

from __future__ import annotations

import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

# scripts/load_sectors.py does not exist yet -- this import is expected to
# raise ImportError until TASK-3.5 lands.
from load_sectors import sector_update_docs, upsert_js  # noqa: E402


def test_sector_update_docs_shapes_deterministic_key_and_preserves_symbol():
    """`symbol` must be carried in the write payload itself -- not merged
    from Arango's side -- because the merge write must not depend on
    `symbol` already being present on an existing document.

    Falsifies if `_key` is anything other than the bare symbol (breaks
    matching the existing `equity/{symbol}` convention every other
    adapter/loader uses), or `symbol` is missing from the payload.
    """
    docs = sector_update_docs([("AAPL", "3674", "Semiconductors")])

    assert docs == [{
        "_key": "AAPL",
        "symbol": "AAPL",
        "sic": "3674",
        "sic_desc": "Semiconductors",
    }]


def test_sector_update_docs_skips_unresolved_symbols():
    """A row SEC had no match for (`sic=None`) must be excluded from the
    returned list entirely, not written as a doc with `sic: null`.

    Falsifies if a null-SIC row produces a doc that would overwrite a
    symbol's real SIC with null on a future re-run before that symbol
    resolves.
    """
    docs = sector_update_docs([
        ("AAPL", "3674", "Semis"),
        ("NOSIC", None, None),
    ])

    assert len(docs) == 1
    assert docs[0] == {
        "_key": "AAPL",
        "symbol": "AAPL",
        "sic": "3674",
        "sic_desc": "Semis",
    }


def test_upsert_js_never_drops_and_never_full_replaces():
    """The write must merge (`overwriteMode:"update"`), never drop a
    collection or full-replace a document.

    Falsifies if the generated text reuses `load_arango.py`'s `bulk()`
    helper, whose `overwriteMode:"replace"` (scripts/load_arango.py:77)
    would silently strip `sic`/`sic_desc` -- and every other field not in
    this script's payload -- off any `equity` document it touches.
    """
    js = upsert_js([{"_key": "AAPL", "symbol": "AAPL", "sic": "3674",
                      "sic_desc": "Semis"}])

    assert "_drop" not in js
    # Whitespace-robust: strip all whitespace before checking for the
    # overwriteMode literal, so a reformatted f-string (e.g. spaces around
    # the colon) can't slip a "replace" write past this check.
    normalized = "".join(js.split())
    assert 'overwriteMode:"replace"' not in normalized
    assert 'overwriteMode:"update"' in normalized
