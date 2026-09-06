"""The shipped exclusion list must exclude funds, not just leveraged products.

D-62 widened neighbour exclusion from leveraged/inverse products to every fund,
because a plain index fund holding the candidate is not independent evidence
about it. That policy lives in a data file, so nothing in the code would catch a
regeneration with the old, narrower rule — this test would.
"""

from __future__ import annotations

from lagmatrix.pipeline.runner import _load_excluded_symbols

# Broad funds that repeatedly won neighbourhood slots before D-62, each holding
# the mega-caps this feed alerts on (D-60 measured 78.1% of slots as funds).
BROAD_FUNDS = {"VUG", "SPYG", "VOOG", "QQQM", "IVW", "MGK", "IYW"}
# Leveraged/inverse products, excluded since D-43 and still required to be.
LEVERAGED = {"FNGU", "FNGD"}


def test_exclusion_list_covers_broad_funds_and_leveraged():
    excluded = _load_excluded_symbols()
    assert BROAD_FUNDS <= excluded, f"missing broad funds: {BROAD_FUNDS - excluded}"
    assert LEVERAGED <= excluded, f"missing leveraged: {LEVERAGED - excluded}"


def test_exclusion_list_does_not_swallow_operating_companies():
    """The name-based classifier is a conjunction for a reason (D-43)."""
    excluded = _load_excluded_symbols()
    # real companies whose names contain fund-ish or leverage-ish words
    for sym in ("BBW", "TXG", "RARE", "UCTT", "UGP"):
        assert sym not in excluded, f"{sym} is an operating company, not a fund"
