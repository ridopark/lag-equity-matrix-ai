"""Re-read `lagmatrix.filing_mention.relation` from the stored passages.

D-72 stored the passage verbatim and called the label disposable precisely so
this could happen without touching EDGAR again. D-78 is that re-read: the
relation now comes from the one sentence that names the counterparty, not from
the +/-420-char window, which spanned enough text that a competitor list two
sentences from a customer mention scored as a customer.

Read-modify-write over ~1,200 rows, in one transaction. `passage` is never
written, so this is repeatable and reversible by re-running with an older
classifier.

    uv run python scripts/reclassify_relations.py --dry-run
    uv run python scripts/reclassify_relations.py
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from lagmatrix.edgar.relations import classify  # noqa: E402

HOST = "ridopark@192.168.10.123"
PSQL = ("kubectl -n copytrade exec -i postgres-0 -- "
        "psql -U temporal -d orchestrator -q -v ON_ERROR_STOP=1")
SEP = "\x1f"


def pg(sql: str, tuples: bool = True) -> str:
    cmd = PSQL + (f" -t -A -F'{SEP}'" if tuples else "")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, cmd],
                       input=sql, capture_output=True, text=True, timeout=900)
    if r.returncode or "ERROR:" in r.stderr:
        sys.exit(f"psql failed:\n{r.stderr[:900]}")
    return r.stdout


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report the reclassification without writing")
    args = ap.parse_args()

    rows = [ln.split(SEP) for ln in pg(
        "SELECT id, counterparty, regexp_replace(passage, E'\\\\s+', ' ', 'g') "
        "FROM lagmatrix.filing_mention;").splitlines() if ln.count(SEP) == 2]
    print(f"  {len(rows)} stored mentions")

    counts: collections.Counter[str] = collections.Counter()
    updates = []
    for rid, named, passage in rows:
        rel, sentence, pct = classify(passage, named)
        counts[rel] += 1
        updates.append((rid, rel, pct))

    for rel, n in counts.most_common():
        print(f"    {n:5}  {100 * n / len(rows):5.1f}%  {rel}")
    kept = counts["customer"]
    print(f"  supplies_to edges after this: {kept} "
          f"(was {len(rows)}, dropping {len(rows) - kept})")

    if args.dry_run:
        print("  --dry-run: nothing written")
        return

    # One statement, one transaction: a half-reclassified table would leave the
    # graph loader silently mixing both labelling schemes.
    values = ",".join(
        f"({rid},'{rel}'::text,{'NULL' if pct is None else pct}::numeric)"
        for rid, rel, pct in updates)
    pg(f"""BEGIN;
UPDATE lagmatrix.filing_mention m
   SET relation = v.rel, pct_revenue = v.pct, confidence = 'sentence'
  FROM (VALUES {values}) AS v(id, rel, pct)
 WHERE m.id = v.id::bigint;
COMMIT;""", tuples=False)
    print("  written")


if __name__ == "__main__":
    main()
