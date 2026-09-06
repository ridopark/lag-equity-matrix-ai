"""Fetch 1-minute bars for the intraday lead-lag test (D-59).

Pulls only what the test needs: for each alert date, the candidates alerting
that day plus the top-20 neighbours the *daily* pipeline already chose for them,
plus a liquidity-matched random control set per candidate.

Neighbour selection is not redone here. It comes from `graph_retriever` running
on data that ends strictly before the alert date, so no intraday information can
leak into which names are considered neighbours.

Usage:  uv run python scripts/fetch_minute.py [--out data/minute.parquet]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from datetime import datetime, time

import pandas as pd
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from lagmatrix.adapters.candidates import ExternalSignals
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.graph_retriever import retrieve_neighbourhood
from lagmatrix.pipeline.runner import _load_excluded_symbols

BATCH = 200
PLAN_PATH = "data/minute-plan.json"


class _Rt:
    """Minimal stand-in for langgraph's Runtime — the node only reads .context."""

    def __init__(self, ctx):
        self.context = ctx


def build_plan(closes: pd.DataFrame, seed: int = 20260906,
               extra_excluded: frozenset[str] = frozenset()) -> dict:
    """{alert_date -> {candidate -> {"neighbours": [...], "control": [...]}}}"""
    import numpy as np

    rng = np.random.default_rng(seed)
    cands = ExternalSignals().candidates()
    universe = set(closes.columns)
    ctx = LagMatrixContext(
        closes=closes,
        signal_universe={c.symbol for c in cands},
        excluded_symbols=_load_excluded_symbols() | extra_excluded,
    )
    # median dollar volume, for liquidity matching the control set
    dv = pd.read_parquet("data/bars.parquet").groupby("symbol").dollar_vol.median()

    plan: dict = {}
    for c in cands:
        out = retrieve_neighbourhood({"candidate": c}, _Rt(ctx))
        edges = next(iter(out["lag_edges_by_key"].values()), [])
        nbrs = [e.leader for e in edges]
        if not nbrs:
            continue
        # control: same count, drawn from the same universe, matched on liquidity
        target = dv.reindex(nbrs).dropna()
        pool = dv.drop(index=[s for s in [*nbrs, c.symbol] if s in dv.index], errors="ignore")
        pool = pool[pool.index.isin(universe)]
        control = []
        for want in target:
            near = (pool - want).abs().nsmallest(25).index
            pick = str(rng.choice(near))
            control.append(pick)
            pool = pool.drop(index=pick, errors="ignore")
        plan.setdefault(c.as_of.isoformat(), {})[c.symbol] = {
            "neighbours": nbrs, "control": control,
        }
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/minute.parquet")
    ap.add_argument("--exclude-funds", action="store_true",
                    help="drop all funds from neighbour selection (D-60)")
    args = ap.parse_args()

    closes = pd.read_parquet("data/bars.parquet").pivot_table(
        index="timestamp", columns="symbol", values="close"
    )
    extra = frozenset()
    if args.exclude_funds:
        import csv as _csv
        with open('data/funds.csv') as fh:
            extra = frozenset(r['symbol'] for r in _csv.DictReader(fh))
        print(f'  excluding {len(extra)} funds from neighbour selection')
    plan = build_plan(closes, extra_excluded=extra)
    plan_path = PLAN_PATH.replace(".json", "-nofunds.json") if args.exclude_funds else PLAN_PATH
    pathlib.Path(plan_path).write_text(json.dumps(plan, indent=1))
    days = sorted(plan)
    syms_per_day = {d: sorted({s for v in plan[d].values()
                               for s in [*v["neighbours"], *v["control"]]} | set(plan[d]))
                    for d in days}
    total = sum(len(v) for v in syms_per_day.values())
    print(f"  plan: {len(days)} alert dates, {total} symbol-days, "
          f"{len(plan)} dates with candidates")

    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                       os.environ["ALPACA_SECRET_KEY"])
    frames = []
    for i, d in enumerate(days, 1):
        syms = syms_per_day[d]
        day = datetime.fromisoformat(d)
        start = datetime.combine(day, time(13, 30))   # 09:30 ET in UTC
        end = datetime.combine(day, time(20, 5))      # 16:05 ET in UTC
        got = 0
        for j in range(0, len(syms), BATCH):
            chunk = syms[j:j + BATCH]
            r = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=chunk, timeframe=TimeFrame.Minute,
                start=start, end=end, adjustment="all", feed="sip"))
            df = r.df
            if df is None or df.empty:
                continue
            df = df.reset_index()[["symbol", "timestamp", "close"]]
            frames.append(df)
            got += len(df)
        print(f"    {i}/{len(days)} {d}: {len(syms)} symbols, {got} bars")

    bars = pd.concat(frames, ignore_index=True)
    bars.to_parquet(args.out, compression="zstd", compression_level=6)
    print(f"  wrote {args.out}: {len(bars):,} rows, "
          f"{bars.symbol.nunique()} symbols, "
          f"{os.path.getsize(args.out)/1e6:.1f} MB")


if __name__ == "__main__":
    main()
