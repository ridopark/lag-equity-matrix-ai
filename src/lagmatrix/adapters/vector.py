"""Vector store: news, filings, and earnings-call passages.

Backed by Qdrant. Swap this module (not its callers) to move to another store.
"""

from lagmatrix.domain.models import NewsChunk


class NewsIndex:
    def search(self, query: str, symbols: list[str], limit: int) -> list[NewsChunk]:
        raise NotImplementedError

    def upsert(self, chunks: list[NewsChunk]) -> None:
        raise NotImplementedError
