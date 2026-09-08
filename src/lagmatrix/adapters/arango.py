"""ArangoDB: the market topology graph.

Collections
-----------
equity        (vertex)  — one per symbol.
supplies_to   (edge)    — supplier -> customer, with filing_date, pct_revenue (D-72).

Direction, per D-73: edges run supplier -> customer (`_from`=supplier,
`_to`=customer), so an INBOUND traversal from the candidate (customer/leader)
reaches its suppliers (its laggers).

Queries live here as AQL so the traversal depth/filters stay in one place.
"""

from __future__ import annotations

from datetime import date

from arango.database import StandardDatabase

from lagmatrix.domain.models import LagEdge

# Per-path (not per-edge) point-in-time guard: `ALL` requires every filing_date
# on the path to be <= as_of, so a path is only traversable once every hop on
# it has actually been filed. Rows are collected per reached symbol so a
# vertex found by more than one path is only reported once, at its shortest
# depth (mirrors scripts/capture_showcase.py's TRAVERSAL_AQL).
_LAGGERS_OF_AQL = """
FOR v, e, p IN 1..@max_hops INBOUND @start supplies_to
  FILTER p.edges[*].filing_date ALL <= @as_of
  COLLECT symbol = v._key INTO g
  LET paths = g[*].p
  LET depth = MIN(FOR x IN paths RETURN LENGTH(x.edges))
  LET best = FIRST(FOR x IN paths
                     FILTER LENGTH(x.edges) == depth
                     SORT TO_NUMBER(x.edges[-1].pct_revenue) DESC
                     LIMIT 1 RETURN x)
  RETURN {symbol, lag_days: depth, pct_revenue: best.edges[-1].pct_revenue}
"""


class ArangoTopology:
    """Thin wrapper over python-arango for leader -> lagger traversal."""

    def __init__(self, db: StandardDatabase) -> None:
        self.db = db

    def laggers_of(self, leader: str, max_hops: int, as_of: date) -> list[LagEdge]:
        cursor = self.db.aql.execute(
            _LAGGERS_OF_AQL,
            bind_vars={
                "start": f"equity/{leader}",
                "max_hops": max_hops,
                "as_of": as_of.isoformat(),
            },
        )
        return [
            LagEdge(
                leader=leader,
                lagger=row["symbol"],
                correlation=0.0,
                lag_days=row["lag_days"],
                beta=float(row["pct_revenue"]) / 100 if row["pct_revenue"] else 0.0,
                relation="supplier",
            )
            for row in cursor
        ]

    def upsert_edge(self, edge: LagEdge) -> None:
        raise NotImplementedError
