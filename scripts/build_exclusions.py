"""Identify leveraged and inverse products, so they stay out of neighbourhoods.

A correlation-built graph cannot tell a supplier from a derivative of the
candidate itself. AAPD (-1x AAPL) and AAPU (2x AAPL) correlate with AAPL almost
perfectly because they *are* AAPL, so they carry no independent information —
they inflate an evidence count without adding evidence.

Classification is name-based, from Alpaca's own asset metadata (D-16). A name
must look like a fund AND carry a leverage or direction token; either alone is
not enough. That conjunction is what keeps Build-A-Bear, 10x Genomics,
Ultragenyx, Ultra Clean and Ultrapar out of the list.

Usage:  uv run python scripts/build_exclusions.py
Output: data/excluded-etfs.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest

FUND = re.compile(
    r"\b(etfs?|etns?|trusts?|funds?|shares?|proshares|direxion|microsectors"
    r"|graniteshares|tidal|themes|tradr|defiance)\b",
    re.I,
)
LEVERAGE = re.compile(
    r"(\b\d+(\.\d+)?x\b|-\d+(\.\d+)?x\b|\bbull\b|\bbear\b|\bultra\w*\b"
    r"|\binverse\b|\bleveraged\b|\bshort\b)",
    re.I,
)


def is_leveraged_or_inverse(name: str | None) -> bool:
    """True only when the name reads as a fund *and* carries a leverage token."""
    return bool(name) and bool(FUND.search(name)) and bool(LEVERAGE.search(name))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/excluded-etfs.csv")
    ap.add_argument("--universe", default="data/universe.csv")
    args = ap.parse_args()

    try:
        tc = TradingClient(
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True
        )
    except KeyError as e:
        sys.exit(f"missing env var {e}")

    assets = {
        a.symbol: a
        for a in tc.get_all_assets(
            GetAssetsRequest(status="active", asset_class="us_equity")
        )
    }
    with open(args.universe) as fh:
        universe = [r["symbol"] for r in csv.DictReader(fh)]

    rows = [
        {"symbol": s, "name": assets[s].name}
        for s in universe
        if s in assets and is_leveraged_or_inverse(assets[s].name)
    ]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "name"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["symbol"]))

    print(f"wrote {args.out}")
    print(f"  universe {len(universe)} | excluded {len(rows)} "
          f"({len(rows) / len(universe):.1%})")


if __name__ == "__main__":
    main()
