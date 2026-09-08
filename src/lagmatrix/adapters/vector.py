"""Vector store: news, filings, and earnings-call passages.

Backed by ArangoDB's `APPROX_NEAR_COSINE` vector index over sentence-transformer
embeddings (see `scripts/load_vectors.py`), not Qdrant — the query text is
embedded here with the same `all-MiniLM-L6-v2` model the corpus was indexed
with, so a query is compared against articles in the same embedding space.

Swap this module (not its callers) to move to another store.
"""

from __future__ import annotations

from datetime import date

from arango.database import StandardDatabase
from sentence_transformers import SentenceTransformer

from lagmatrix.domain.models import NewsChunk

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Mirrors scripts/capture_showcase.py's VECTOR_AQL: LET the ANN score first,
# then a single FILTER (combining the symbol and as_of predicates with AND --
# ArangoDB rejects APPROX_NEAR_COSINE if two separate FILTER statements sit
# between it and SORT/LIMIT, with ERR 1554), then SORT/LIMIT on it, so the
# vector index is used rather than a brute-force scan.
_SEARCH_AQL = """
FOR a IN article
  LET score = APPROX_NEAR_COSINE(a.embedding, @vector)
  FILTER LENGTH(INTERSECTION(a.symbols, @symbols)) > 0 AND a.date < @as_of
  SORT score DESC
  LIMIT @limit
  RETURN {doc_id: a._key, text: a.headline, published_at: a.date, score}
"""


class NewsIndex:
    def __init__(self, db: StandardDatabase) -> None:
        self.db = db
        self._model = SentenceTransformer(EMBEDDING_MODEL)

    def search(
        self, query: str, symbols: list[str], limit: int, as_of: date
    ) -> list[NewsChunk]:
        vector = self._model.encode([query], normalize_embeddings=True)[0].tolist()
        cursor = self.db.aql.execute(
            _SEARCH_AQL,
            bind_vars={
                "vector": vector,
                "symbols": symbols,
                "limit": limit,
                "as_of": as_of.isoformat(),
            },
        )
        return [
            NewsChunk(
                doc_id=row["doc_id"],
                symbol=symbols[0],
                text=row["text"],
                published_at=row["published_at"],
                score=row["score"],
            )
            for row in cursor
        ]

    def upsert(self, chunks: list[NewsChunk]) -> None:
        raise NotImplementedError
