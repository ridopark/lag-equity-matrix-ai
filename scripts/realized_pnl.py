"""Realized P&L of the upstream signal feed, from actual broker fills.

Every experiment in this project asked whether the graph feature predicts.
None asked whether the SIGNAL does. The fills have been in audit_log all along.

Three things this has to get right, each established by looking first:

  - Tenants are SEPARATE ACCOUNTS, not duplicated rows. staging_paper, dev and
    paper_jinchiul are paper; prod_real, prod-kipark and prod-jinchul are real
    money. Pooling them would report paper fills as trading performance.
  - Exits are PARTIAL and often incomplete: 11 contracts in, 6 + 3 out. P&L is
    computed on the matched quantity only, never assuming what happened to the
    remainder.
  - `filled_qty` on entry and `qty_filled` on exit are contracts; the option
    multiplier is 100.

Usage:  uv run python scripts/realized_pnl.py
"""

from __future__ import annotations

import io
import subprocess
import sys

import pandas as pd

HOST = "ridopark@192.168.10.123"
PAPER = {"staging_paper", "dev", "paper_jinchiul"}
MULT = 100


def q(sql: str) -> pd.DataFrame:
    inner = "'" + " ".join(sql.split()).replace("'", "'\"'\"'") + "'"
    remote = ("kubectl -n copytrade exec postgres-0 -- "
              f"psql -U temporal -d orchestrator --csv -c {inner}")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, remote],
                       capture_output=True, text=True, timeout=600)
    if r.returncode:
        sys.exit(f"query failed:\n{r.stderr[:800]}")
    return pd.read_csv(io.StringIO(r.stdout))


def main() -> None:
    entries = q("""
      SELECT DISTINCT ON (subject->>'broker_order_id')
             tenant_id, subject->>'broker_order_id' oid,
             subject->>'option_symbol' contract,
             (subject->>'avg_fill_price')::numeric px,
             (subject->>'filled_qty')::numeric qty,
             occurred_at
      FROM audit_log WHERE kind='EntryFilled' AND subject ? 'avg_fill_price'
        AND subject ? 'option_symbol'
      ORDER BY subject->>'broker_order_id', occurred_at""")
    exits = q("""
      SELECT DISTINCT ON (subject->>'broker_order_id')
             tenant_id, subject->>'broker_order_id' oid,
             subject->>'option_symbol' contract,
             (subject->>'avg_fill_price')::numeric px,
             (subject->>'qty_filled')::numeric qty,
             occurred_at
      FROM audit_log WHERE kind='PartialExitFilled' AND subject ? 'avg_fill_price'
        AND subject ? 'option_symbol'
      ORDER BY subject->>'broker_order_id', occurred_at""")

    for d in (entries, exits):
        d["occurred_at"] = pd.to_datetime(d.occurred_at, utc=True, format="ISO8601")
        d["contract"] = d.contract.str.strip()

    ein = entries.groupby(["tenant_id", "contract"]).apply(
        lambda g: pd.Series({
            "entry_qty": g.qty.sum(),
            "entry_vwap": (g.px * g.qty).sum() / g.qty.sum(),
            "entered_at": g.occurred_at.min()}), include_groups=False)
    xout = exits.groupby(["tenant_id", "contract"]).apply(
        lambda g: pd.Series({
            "exit_qty": g.qty.sum(),
            "exit_vwap": (g.px * g.qty).sum() / g.qty.sum(),
            "exited_at": g.occurred_at.max()}), include_groups=False)

    t = ein.join(xout, how="inner").reset_index()
    t["matched_qty"] = t[["entry_qty", "exit_qty"]].min(axis=1)
    t["ret"] = t.exit_vwap / t.entry_vwap - 1.0
    t["pnl"] = (t.exit_vwap - t.entry_vwap) * t.matched_qty * MULT
    t["hold_h"] = (t.exited_at - t.entered_at).dt.total_seconds() / 3600
    t["real"] = ~t.tenant_id.isin(PAPER)
    t["ticker"] = t.contract.str.extract(r"^([A-Z]+)")

    t.to_csv("data/realized-pnl.csv", index=False)
    print(f"  {len(t)} closed round-trips across {t.tenant_id.nunique()} accounts")
    print("  wrote data/realized-pnl.csv\n")

    for label, g in (("REAL MONEY", t[t.real]), ("paper", t[~t.real])):
        if g.empty:
            continue
        wins = (g.ret > 0).mean()
        print(f"  {label}  ({', '.join(sorted(g.tenant_id.unique()))})")
        print(f"    trades          {len(g)}")
        print(f"    total P&L       ${g.pnl.sum():,.0f}")
        print(f"    mean return     {g.ret.mean():+.1%}   median {g.ret.median():+.1%}")
        print(f"    win rate        {wins:.1%}")
        print(f"    best / worst    {g.ret.max():+.0%} / {g.ret.min():+.0%}")
        print(f"    median hold     {g.hold_h.median():.1f} h")
        won, lost = g[g.ret > 0], g[g.ret <= 0]
        if len(won) and len(lost):
            print(f"    avg win {won.ret.mean():+.1%}  avg loss {lost.ret.mean():+.1%}  "
                  f"payoff {abs(won.ret.mean()/lost.ret.mean()):.2f}")
        print()

    real = t[t.real]
    if len(real) >= 10:
        print("  REAL MONEY, by ticker (>=3 trades)")
        for tk, g in real.groupby("ticker"):
            if len(g) >= 3:
                print(f"    {tk:<6} n={len(g):>3}  mean {g.ret.mean():+7.1%}  "
                      f"win {(g.ret>0).mean():5.1%}  P&L ${g.pnl.sum():>9,.0f}")


if __name__ == "__main__":
    main()
