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

from lagmatrix.domain.models import ComovementEdge, LagEdge

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


# Point-in-time (D-16, D-82): "most recent snapshot at or before as_of", not
# "the snapshot dated exactly as_of" -- matches ArangoTopology.laggers_of's
# `<= as_of` convention. `as_of` is stored as an ISO date string (YYYY-MM-DD),
# which sorts/compares correctly with plain `<=`/`DESC` since lexicographic
# order matches chronological order for that format. The snapshot is resolved
# first, then the main query is pinned to that single `as_of` so two
# snapshots of the same pair are never blended. Matches on `a`/`b` directly
# rather than `_from`/`_to`, since only one direction per pair is stored
# (D-95) and `movers_with` must find a symbol on either side.
_MOVERS_WITH_AQL = """
LET snapshot = FIRST(
  FOR e IN moves_with
    FILTER e.as_of <= @as_of
    FILTER e.a == @symbol OR e.b == @symbol
    SORT e.as_of DESC
    LIMIT 1
    RETURN e.as_of
)
FOR e IN moves_with
  FILTER e.as_of == snapshot
  FILTER e.a == @symbol OR e.b == @symbol
  FILTER ABS(e.corr) >= @min_abs_corr
  RETURN e
"""


def upsert_comovement(
    db: StandardDatabase, edges: list[ComovementEdge], as_of: date
) -> None:
    """Write co-movement edges into `moves_with`, creating it if absent and
    never dropping it (D-97). Keyed deterministically on (a, b, as_of) so a
    re-run for the same date replaces the existing document instead of
    duplicating it.
    """
    if not db.has_collection("moves_with"):
        db.create_collection("moves_with", edge=True)
    collection = db.collection("moves_with")
    for edge in edges:
        collection.insert(
            {
                "_key": f"{edge.a}_{edge.b}_{as_of.isoformat()}",
                "_from": f"equity/{edge.a}",
                "_to": f"equity/{edge.b}",
                "a": edge.a,
                "b": edge.b,
                "corr": edge.corr,
                "n_sessions": edge.n_sessions,
                "ci_low": edge.ci_low,
                "ci_high": edge.ci_high,
                "flag": edge.flag,
                "as_of": as_of.isoformat(),
            },
            overwrite=True,
        )


_EDGE_ENDPOINTS_AQL = """
FOR e IN @@collection
  RETURN [e._from, e._to]
"""


def embedding_universe(db: StandardDatabase, alert_symbols: list[str]) -> list[str]:
    """The symbols the embedding corpus should cover: both endpoints of every
    `supplies_to` and `moves_with` edge, unioned with `alert_symbols` (never
    replacing it, so a symbol present only in the alert set still comes
    back). Either collection can be absent (fresh database) or empty; both
    degrade to the alert set alone rather than raising. Returned as a
    sorted, de-duplicated list of plain symbols (`equity/AAPL`'s `_key`).
    """
    symbols = set(alert_symbols)
    for collection in ("supplies_to", "moves_with"):
        if not db.has_collection(collection):
            continue
        cursor = db.aql.execute(
            _EDGE_ENDPOINTS_AQL, bind_vars={"@collection": collection}
        )
        for frm, to in cursor:
            symbols.add(frm.split("/", 1)[1])
            symbols.add(to.split("/", 1)[1])
    return sorted(symbols)


def movers_with(
    db: StandardDatabase, symbol: str, as_of: date, min_abs_corr: float = 0.5
) -> list[ComovementEdge]:
    """Co-movement edges involving `symbol` as measured `as_of` that date,
    filtered on `abs(corr) >= min_abs_corr`. Empty list, not an error, if
    `symbol` has no stored edges.
    """
    if not db.has_collection("moves_with"):
        return []
    cursor = db.aql.execute(
        _MOVERS_WITH_AQL,
        bind_vars={
            "symbol": symbol,
            "as_of": as_of.isoformat(),
            "min_abs_corr": min_abs_corr,
        },
    )
    return [
        ComovementEdge(
            a=row["a"],
            b=row["b"],
            corr=row["corr"],
            n_sessions=row["n_sessions"],
            ci_low=row["ci_low"],
            ci_high=row["ci_high"],
            flag=row["flag"],
        )
        for row in cursor
    ]
