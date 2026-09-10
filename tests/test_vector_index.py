"""PHASE-6 (PLAN-2026-09-07-graphrag-showcase): `NewsIndex.search` — real
cosine similarity search over real sentence-transformer embeddings, backed by
ArangoDB's `APPROX_NEAR_COSINE` vector index (confirmed enabled and working
against the live homelab instance; probed here against a throwaway
collection with a training set as small as this fixture's, since the real
`article` collection's 47,640 rows are not something these tests may depend
on or risk corrupting).

`search(query: str, symbols: list[str], limit: int, as_of: date) -> list[NewsChunk]`
-- this project's own `.env.example`/`config.embedding_model` default
(`all-MiniLM-L6-v2`) is what fixture articles are embedded with here, so a
`NewsIndex` implementation embedding the query with the same model is
comparing like with like. `as_of` is a point-in-time bound, mirroring
`scripts/capture_showcase.py`'s `VECTOR_AQL` (`FILTER a.date < @as_of`) and
`ArangoTopology.leaders_of`'s own per-path `as_of` guard: only articles
published strictly before the candidate's date may come back, so a retrieval
can never leak future information into a past assessment.

Same skip-if-unreachable pattern as `test_arango_topology.py`, and the same
database-level isolation: `NewsIndex` queries the `article` collection by
name (matching `scripts/capture_showcase.py`'s real AQL) with no constructor
parameter for it (CLAUDE.md's "no configurability that wasn't requested"),
so it is a dedicated, disposable database -- not a renamed collection inside
the real `lagmatrix` db -- that keeps this fixture off the real 47,640-row
corpus, and cannot reach it even by mistake. CI's PHASE-8 service container
is where this actually runs.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import arango_db_or_skip, embed_texts
from lagmatrix.adapters.vector import NewsIndex
from lagmatrix.domain.models import NewsChunk

# A disposable database of its own -- never the real `lagmatrix` -- so the
# collection name below can match production (`article`, which NewsIndex
# queries by hardcoded name) without touching the real 47,640-row corpus.
ARANGO_DB_NAME = "test_vector_index"
ARTICLE = "article"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DIM = 384

CHIP_TEXT = "Chip supply shortage hits semiconductor makers as component lead times stretch"
DIVIDEND_TEXT = "The board declared a quarterly cash dividend payable next month"
AS_OF = date(2025, 3, 3)  # strictly after both "X"-tagged fixture articles above

# Lookahead-leak fixture: symbol "F", disjoint from "X"/"Y" above so these
# tests cannot be satisfied by the wrong articles passing a symbol filter.
LOOKAHEAD_SYMBOL = "F"
LOOKAHEAD_QUERY = "chip supply shortage"
LOOKAHEAD_AS_OF = date(2025, 6, 15)
# On-topic but not a duplicate of the query -- a plausible, if lesser, match
# on embeddings alone -- and dated before the cutoff, so it is the one
# article a correct filter must return.
BEFORE_CUTOFF_TEXT = "Component lead times stretch as chip supply tightens for makers"
# An exact duplicate of the query text, which guarantees the highest possible
# cosine score against it -- so if either of these leaks past the `as_of`
# filter, it cannot lose to BEFORE_CUTOFF_TEXT by chance.
AFTER_CUTOFF_TEXT = LOOKAHEAD_QUERY
ON_CUTOFF_TEXT = LOOKAHEAD_QUERY


@pytest.fixture(scope="module")
def index():
    """Connect to a real ArangoDB, embed two fixture articles with the real
    model, and index them with a real (if tiny) `APPROX_NEAR_COSINE` vector
    index. Skips cleanly if ArangoDB is not reachable.

    Both fixture articles are tagged only "X" -- one genuinely about a chip
    supply shortage, one an unrelated dividend announcement -- so a metadata
    filter on symbol alone cannot distinguish them; only the embeddings can.
    No article is tagged "Y" at all, for the empty-result test.

    Three more articles, tagged "F", straddle `LOOKAHEAD_AS_OF` for the
    point-in-time tests: one before the cutoff, one exactly on it, one after
    -- the latter two embedded as exact duplicates of the query so a missing
    or ignored `as_of` filter cannot pass by ranking alone.
    """
    db = arango_db_or_skip(ARANGO_DB_NAME)

    if db.has_collection(ARTICLE):
        db.delete_collection(ARTICLE)
    db.create_collection(ARTICLE)
    articles = db.collection(ARTICLE)

    texts = [CHIP_TEXT, DIVIDEND_TEXT, BEFORE_CUTOFF_TEXT,
             AFTER_CUTOFF_TEXT, ON_CUTOFF_TEXT]
    vectors = embed_texts(texts)
    articles.insert({
        "_key": "chip-article", "date": "2025-03-01", "headline": CHIP_TEXT,
        "summary": "", "symbols": ["X"], "embedding": [round(float(x), 6) for x in vectors[0]],
    })
    articles.insert({
        "_key": "dividend-article", "date": "2025-03-02", "headline": DIVIDEND_TEXT,
        "summary": "", "symbols": ["X"], "embedding": [round(float(x), 6) for x in vectors[1]],
    })
    articles.insert({
        "_key": "before-cutoff-article", "date": "2025-06-14", "headline": BEFORE_CUTOFF_TEXT,
        "summary": "", "symbols": [LOOKAHEAD_SYMBOL],
        "embedding": [round(float(x), 6) for x in vectors[2]],
    })
    articles.insert({
        "_key": "after-cutoff-article", "date": "2025-06-16", "headline": AFTER_CUTOFF_TEXT,
        "summary": "", "symbols": [LOOKAHEAD_SYMBOL],
        "embedding": [round(float(x), 6) for x in vectors[3]],
    })
    articles.insert({
        "_key": "on-cutoff-article", "date": "2025-06-15", "headline": ON_CUTOFF_TEXT,
        "summary": "", "symbols": [LOOKAHEAD_SYMBOL],
        "embedding": [round(float(x), 6) for x in vectors[4]],
    })
    # nLists must not exceed the training set size; a fixture this small needs
    # a correspondingly tiny value (probed directly against the live instance
    # with a 3-row/4-dim throwaway collection: nLists=1 builds and queries
    # cleanly), not the production 47,640-row corpus's nLists=64.
    articles.add_index({
        "type": "vector", "fields": ["embedding"],
        "params": {"metric": "cosine", "dimension": DIM, "nLists": 1},
    })

    return NewsIndex(db)


def test_search_ranks_the_semantically_related_article_first(index):
    """A query about a chip supply shortage must rank the chip-shortage
    fixture article above the unrelated dividend one, even though both share
    the only symbol filter applied.

    Falsifies if ranking is arbitrary/insertion-order rather than driven by
    the embeddings -- e.g. if the dividend article ranks first or the two
    tie, which would mean similarity isn't actually doing the work.
    """
    results = index.search(query="chip supply shortage", symbols=["X"], limit=5, as_of=AS_OF)

    assert len(results) == 2
    assert all(isinstance(r, NewsChunk) for r in results)
    assert results[0].doc_id == "chip-article"
    assert results[0].score > results[1].score


def test_search_on_symbol_with_no_articles_returns_empty(index):
    """A symbol that was never seeded must come back as `[]`, not another
    symbol's articles and not an error.

    Falsifies if this returns the "X"-tagged articles anyway (metadata filter
    is advisory / not actually applied) or raises.
    """
    results = index.search(query="chip supply shortage", symbols=["Y"], limit=5, as_of=AS_OF)

    assert results == []


def test_search_excludes_article_published_after_as_of_even_when_better_match(index):
    """An article published after the candidate's `as_of` must never come
    back, even when it is a stronger semantic match than every article that
    is actually eligible.

    `after-cutoff-article` is dated 2025-06-16 (after `LOOKAHEAD_AS_OF` =
    2025-06-15) and is an exact-text duplicate of the query, so on
    embeddings alone it would rank above `before-cutoff-article`. Only a
    real point-in-time filter keeps it out.

    Falsifies if: `search` has no `as_of` parameter at all (raises
    `TypeError` for the unexpected keyword) -- today's implementation -- or,
    once one exists, if it is accepted but not applied, letting
    `after-cutoff-article` appear in the results.
    """
    results = index.search(
        query=LOOKAHEAD_QUERY, symbols=[LOOKAHEAD_SYMBOL], limit=5, as_of=LOOKAHEAD_AS_OF
    )

    doc_ids = {r.doc_id for r in results}
    assert doc_ids == {"before-cutoff-article"}


def test_search_excludes_article_published_exactly_on_as_of(index):
    """An article dated exactly on the candidate's `as_of` must be excluded
    -- the bound is strictly-before, not on-or-before.

    `on-cutoff-article` is dated 2025-06-15, equal to `LOOKAHEAD_AS_OF`, and
    is also an exact-text duplicate of the query (the strongest possible
    match), so it cannot be excluded by rank -- only by date.

    Falsifies if: `on-cutoff-article` appears in the results, which would
    mean the filter (if any) uses `<=` rather than `<`.
    """
    results = index.search(
        query=LOOKAHEAD_QUERY, symbols=[LOOKAHEAD_SYMBOL], limit=5, as_of=LOOKAHEAD_AS_OF
    )

    doc_ids = {r.doc_id for r in results}
    assert "on-cutoff-article" not in doc_ids
