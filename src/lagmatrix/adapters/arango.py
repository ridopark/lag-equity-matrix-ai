"""ArangoDB: the market topology graph.

Collections
-----------
equities   (vertex)  — symbol, name, sector, industry, market_cap
lags       (edge)    — _from/_to equity, correlation, lag_days, beta, relation

Queries live here as AQL so the traversal depth/filters stay in one place.
"""

from lagmatrix.domain.models import LagEdge


class ArangoTopology:
    """Thin wrapper over python-arango for leader -> lagger traversal."""

    def laggers_of(self, leader: str, max_hops: int) -> list[LagEdge]:
        raise NotImplementedError

    def upsert_edge(self, edge: LagEdge) -> None:
        raise NotImplementedError
