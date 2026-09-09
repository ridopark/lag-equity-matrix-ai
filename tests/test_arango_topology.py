"""PHASE-3 (PLAN-2026-09-07-graphrag-showcase, as corrected): `ArangoTopology.
laggers_of` -- real multi-hop, directed, point-in-time AQL graph traversal.

This implements the method already declared in `adapters/arango.py`
(`laggers_of(leader, max_hops)`, reserved for the unbuilt `MarketScan` mode),
adding the `as_of` parameter it lacked. It does **not** add a new
`leaders_of` method -- the plan originally proposed one on the premise that a
candidate is a potential *lagger*, but D-72 (only AAPL has real depth as a
customer among the 24 alert tickers) plus D-73 (the customer leads the
supplier, per Cohen & Frazzini) together mean every real candidate sits at
the *customer* end of `supplies_to` and is therefore a **leader**, with its
suppliers as its **laggers**. Given a leader, find the things it leads is
exactly `laggers_of`'s job.

Direction, per D-73 (`docs/spikes/overall.md`): edges run supplier ->
customer (`_from`=supplier, `_to`=customer). Walking INBOUND from the
candidate (the customer/leader) reaches its suppliers -- its laggers.

Field orientation matches `LagEdge`'s own docstring ("A leader -> lagger
relationship") and the method's own parameter name: `leader` is fixed to the
candidate passed in on every returned edge; `lagger` is the neighbour found,
whichever hop it was found at -- the opposite of `graph_retriever`'s
correlation edges (where the *candidate* is `lagger`), because `laggers_of`
answers a different question about a different role.

The fixture graph is entirely synthetic and does not reuse real company
symbols: an audit of the live `supplies_to` edges (D-78) found several are
not genuine supply relationships (e.g. a divestiture, a competitor listing),
so real symbols/edges are not a safe stand-in even for a self-contained test
fixture.

These tests need a real, reachable ArangoDB and skip cleanly if there isn't
one (D-38: no silent pass). `ArangoTopology` queries the collections
`equity`/`supplies_to` by name (matching `scripts/capture_showcase.py`'s real
AQL) -- no constructor parameter for this, since it would only ever exist to
make a test pass (CLAUDE.md's "no configurability that wasn't requested").
Isolation from the homelab's real data instead happens one level up, at the
*database*: each test connects to its own dedicated, disposable database
(created if absent) and creates `equity`/`supplies_to` inside *that* -- never
inside `lagmatrix`, where the real collections (currently being re-audited,
see D-78) live. Safer than a same-database rename too: a separate database
cannot reach the real `equity`/`supplies_to` even by mistake.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import arango_db_or_skip
from lagmatrix.adapters.arango import ArangoTopology
from lagmatrix.domain.models import LagEdge

# A disposable database of its own -- never the real `lagmatrix` -- so the
# collection names below can match production (`equity`/`supplies_to`, which
# ArangoTopology queries by hardcoded name) without touching production data.
ARANGO_DB_NAME = "test_arango_topology"
VERTEX = "equity"
EDGE = "supplies_to"

# BOT1 -> MID1 -> TOP1: TOP1 is the candidate (customer role, the leader);
# MID1 is its direct supplier (1-hop lagger); BOT1 is MID1's own supplier
# (2-hop lagger). Entirely fictional symbols -- see module docstring.
#
# The edge nearest the candidate (MID1->TOP1) is filed *before* the edge
# farther from it (BOT1->MID1) -- deliberately, so the point-in-time test can
# tell "every edge on the path" apart from a bug that only checks one edge:
# at a date between the two, the near edge alone would let a partial filter
# (checking only the edge nearest the candidate) pass BOT1 through too, where
# the real ALL-quantified guard correctly excludes it. Do not "fix" these
# back to filed-in-chain-order -- that ordering cannot tell the two apart.
MID1_TO_TOP1_FILING = date(2023, 6, 1)
BOT1_TO_MID1_FILING = date(2024, 1, 15)

BETWEEN_FILINGS = date(2023, 9, 1)  # after MID1->TOP1, before BOT1->MID1
AFTER_BOTH = date(2024, 6, 1)


@pytest.fixture(scope="module")
def topology():
    """Connect to a real ArangoDB and seed a throwaway fixture graph.

    Skips (not fails, not passes vacuously) if nothing is reachable at
    `LAGMATRIX_ARANGO_URL` -- true on this dev machine today (the instance is
    only reachable via the homelab's ssh+kubectl route); CI's PHASE-8 service
    container is where this actually runs.
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

    for symbol in ["BOT1", "MID1", "TOP1"]:
        vertices.insert({"_key": symbol})
    edges.insert({
        "_from": f"{VERTEX}/MID1", "_to": f"{VERTEX}/TOP1",
        "filing_date": MID1_TO_TOP1_FILING.isoformat(), "pct_revenue": "20.0",
    })
    edges.insert({
        "_from": f"{VERTEX}/BOT1", "_to": f"{VERTEX}/MID1",
        "filing_date": BOT1_TO_MID1_FILING.isoformat(), "pct_revenue": "15.0",
    })

    return ArangoTopology(db)


def test_one_hop_reaches_direct_supplier_only(topology):
    """`laggers_of("TOP1", max_hops=1, as_of=<after both filings>)` must
    return exactly MID1 -- TOP1's direct supplier, its lagger per D-73 -- via
    INBOUND traversal.

    Falsifies if: MID1 is missing (wrong direction -- e.g. walking OUTBOUND,
    which finds nothing since TOP1 supplies no one), or if BOT1 (two hops
    away) leaks through at `max_hops=1` (hop bound not enforced).
    """
    result = topology.laggers_of("TOP1", max_hops=1, as_of=AFTER_BOTH)

    assert [e.lagger for e in result] == ["MID1"]
    edge = result[0]
    assert isinstance(edge, LagEdge)
    assert edge.leader == "TOP1"
    assert edge.relation == "supplier"
    assert edge.lag_days == 1
    assert edge.correlation == 0.0
    assert edge.beta == pytest.approx(0.20)


def test_two_hops_reaches_the_tier_two_supplier(topology):
    """`laggers_of("TOP1", max_hops=2, as_of=<after both filings>)` must add
    BOT1 (MID1's own supplier, two hops from TOP1) to the one-hop result.

    Falsifies if BOT1 is missing -- this is the test that proves
    `config.max_lag_hops`, carried since the project's first commit and never
    read, finally changes the traversal's result at `max_hops=2` vs `1`.
    """
    result = topology.laggers_of("TOP1", max_hops=2, as_of=AFTER_BOTH)

    by_lagger = {e.lagger: e for e in result}
    assert by_lagger.keys() == {"MID1", "BOT1"}
    assert all(e.leader == "TOP1" for e in result)
    assert by_lagger["MID1"].lag_days == 1
    assert by_lagger["BOT1"].lag_days == 2


def test_as_of_excludes_a_filing_that_has_not_happened_yet(topology):
    """Point-in-time guard: at `as_of` between the two filing waves, TOP1's
    own edge from MID1 has already been filed, but MID1's edge from BOT1 has
    not -- the 2-hop path to BOT1 must be excluded even though the edge
    nearest the candidate (MID1->TOP1) is within the window.

    Falsifies if BOT1 leaks through -- which is what would happen both from a
    missing filter and from a filter that only checks the edge nearest the
    candidate instead of every edge on the path (the ALL-quantified
    per-path check, not a per-edge one -- the near edge alone passes here).
    """
    result = topology.laggers_of("TOP1", max_hops=2, as_of=BETWEEN_FILINGS)

    assert [e.lagger for e in result] == ["MID1"]


def test_laggers_of_unknown_symbol_returns_empty_not_an_error(topology):
    """A candidate with no edges at all must come back as `[]`, not raise.

    Falsifies if this raises (e.g. an AQL/document-not-found error) instead
    of returning an empty list.
    """
    result = topology.laggers_of("NONEXISTENT", max_hops=2, as_of=AFTER_BOTH)

    assert result == []
