"""Extract the upstream signal fires from oh-my-tradeagent's audit_log.

One row per distinct BTO signal — NOT one row per audit_log row. The table
records each alert once per tenant across 6 tenants, so `count(*)` overstates
fires by ~3x (spike 07).

Read-only. Connects to postgres directly via `lagmatrix.pg` (Q-54): the old
`ssh <host> -> kubectl -n copytrade exec postgres-0 -- psql` route needed a
kubeconfig, so nothing running in a pod could use it.

Usage:  uv run python scripts/extract_fires.py [--out data/fires.csv]
"""

from __future__ import annotations

import argparse
import csv
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from lagmatrix import pg  # noqa: E402

# DISTINCT ON collapses the per-tenant duplicates; the earliest occurred_at is
# when we first saw the alert. Direction: a bought call is bullish, a bought put
# bearish (D-31).
SQL = """
SELECT DISTINCT ON (subject->>'signal_id')
       subject->>'signal_id'                       AS signal_id,
       subject->>'ticker'                          AS ticker,
       CASE subject->>'right' WHEN 'C' THEN 'up'
                              WHEN 'P' THEN 'down' END AS direction,
       subject->>'right'                           AS opt_right,
       subject->>'strike'                          AS strike,
       subject->>'expiry'                          AS expiry,
       subject->>'price'                           AS alert_premium,
       subject->>'author'                          AS author,
       (subject->>'posted_at')::timestamptz        AS posted_at,
       min(occurred_at) OVER (PARTITION BY subject->>'signal_id') AS first_seen_at
FROM audit_log
WHERE kind = 'SignalReceived'
  AND subject->>'action' = 'BTO'
  AND subject ? 'ticker'
ORDER BY subject->>'signal_id', occurred_at
"""

ETFS = {"SPY", "QQQ"}


def fetch() -> str:
    """The signal rows, as CSV text, so the parsing below is unchanged.

    `audit_log` is the one trading table this repo reads; the `lagmatrix_ingest`
    role has SELECT on it and nothing else in `public`.
    """
    conn = pg.connect()
    try:
        result = pg.rows(conn, SQL)
        columns = [
            "signal_id", "ticker", "direction", "opt_right", "strike",
            "expiry", "alert_premium", "author", "posted_at", "first_seen_at",
        ]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(columns)
        w.writerows(result)
        return buf.getvalue()
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/fires.csv")
    args = ap.parse_args()

    rows = list(csv.DictReader(io.StringIO(fetch())))
    if not rows:
        sys.exit("no rows returned")

    for r in rows:
        r["is_etf"] = str(r["ticker"] in ETFS)

    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    single = [r for r in rows if r["is_etf"] == "False"]
    undated = [r for r in rows if not r["posted_at"]]
    print(f"wrote {args.out}")
    print(f"  distinct BTO fires : {len(rows)}")
    print(f"  single names (test population, D-31): {len(single)}")
    print(f"  ETFs (excluded from first test)     : {len(rows) - len(single)}")
    print(f"  missing posted_at                   : {len(undated)}")
    print(f"  date range : {min(r['posted_at'] for r in rows if r['posted_at'])[:10]}"
          f" -> {max(r['posted_at'] for r in rows if r['posted_at'])[:10]}")
    print(f"  distinct tickers : {len({r['ticker'] for r in rows})}")


if __name__ == "__main__":
    main()
