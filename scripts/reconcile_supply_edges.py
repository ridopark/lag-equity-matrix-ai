"""One-time reconciliation of `supplies_to` after the Q-64 key fix (Q-65).

Q-64 left the collection holding 818 edges that all carry auto-assigned keys,
because `supplier->customer` was an illegal ArangoDB key and every load
inserted fresh documents. Those 818 cover 410 distinct
`(_from, _to, filing_date)` triples -- the point-in-time record the `as_of`
traversal depends on (D-16/D-72) -- plus 408 redundant copies.

Deduping by PAIR would destroy the history (818 -> 130). This dedupes by
TRIPLE (818 -> 410), which keeps every filing date.

Copies of the same triple are not always identical: 156 triples disagree on
`pct_revenue`, typically one copy carrying a real figure and another `None`.
Precedence, chosen by the owner and stated rather than defaulted into:

  1. prefer a non-null `pct_revenue` over a null one
  2. then the longest `passage` (the longer extract is the better evidence)
  3. then the lowest existing `_key`, purely so the result is deterministic

Order of operations is additive-then-destructive on purpose: the correctly
keyed documents are written FIRST and the traversal re-verified, so the data
is never absent even if the run is interrupted midway.

Usage:  uv run python scripts/reconcile_supply_edges.py [--apply]
        (dry-run by default -- prints what it would do and writes nothing)
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from arango import ArangoClient

from lagmatrix.config import load_settings


def winner(copies: list[dict]) -> dict:
    """Pick the authoritative copy of one (from, to, filing_date) triple."""
    return sorted(
        copies,
        key=lambda d: (
            d.get("pct_revenue") is None,          # non-null first
            -len(d.get("passage") or ""),          # longest passage next
            d["_key"],                             # deterministic tie-break
        ),
    )[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    args = ap.parse_args()

    s = load_settings()
    db = ArangoClient(hosts=s.arango_url).db(
        s.arango_db, username=s.arango_user,
        password=Path("~/.lagmatrix-arango-pw").expanduser().read_text().strip(),
    )
    docs = list(db.aql.execute("FOR e IN supplies_to RETURN e"))
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for d in docs:
        groups[(d["_from"], d["_to"], d.get("filing_date"))].append(d)

    keep, recovered = [], 0
    for (frm, to, filing_date), copies in groups.items():
        w = winner(copies)
        if w.get("pct_revenue") is not None and any(c.get("pct_revenue") is None for c in copies):
            recovered += 1
        keep.append({
            "_key": f"{frm.split('/')[1]}:{to.split('/')[1]}:{filing_date}",
            "_from": frm, "_to": to, "filing_date": filing_date,
            "pct_revenue": w.get("pct_revenue"), "passage": w.get("passage"),
            "counterparty": w.get("counterparty"), "relation": "supplies_to",
        })

    # Only documents whose key is NOT one of the correct keys are removed. A
    # re-run therefore finds nothing to delete and is a no-op -- computing this
    # as "every key currently present" instead made the second run delete the
    # very documents the first run had just written.
    correct = {k["_key"] for k in keep}
    old_keys = [d["_key"] for d in docs if d["_key"] not in correct]
    print(f"  existing edges      : {len(docs):,}")
    print(f"  distinct triples    : {len(keep):,}   (history preserved)")
    print(f"  redundant copies    : {len(docs) - len(keep):,}")
    stale = [d for d in docs if d["_key"] not in {k["_key"] for k in keep}]
    print(f"  stale docs to remove: {len(stale):,}")
    print(f"  pct_revenue rescued : {recovered:,} triples where a null copy would have won")
    have = sum(1 for k in keep if k["pct_revenue"] is not None)
    print(f"  with pct_revenue    : {have:,} of {len(keep):,}")
    if not args.apply:
        print("\n  DRY RUN -- nothing written. Re-run with --apply.")
        return

    coll = db.collection("supplies_to")
    coll.insert_many(keep, overwrite_mode="update")     # additive first
    print(f"\n  wrote {len(keep):,} correctly-keyed documents (count now {coll.count():,})")
    if old_keys:
        coll.delete_many([{"_key": k} for k in old_keys])   # then remove the stale
    print(f"  removed {len(old_keys):,} stale documents (count now {coll.count():,})")


if __name__ == "__main__":
    main()
