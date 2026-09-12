"""Compute the weak/strong PMI cutoff for the three-state co-mention feature.

D-136 found that co-mention relatedness carries two effects with OPPOSITE
signs -- merely having an edge predicts failure (-29.6pp), while a HIGHER PMI
among edged pairs predicts retention (AUC 0.7451) -- and that collapsing them
into one numeric column averages them to nothing (0.4803, D-135's mistake).
A three-state encoding needs a cutoff separating "weak" from "strong".

Band choice: this reads the **0.3-0.4** correlation-magnitude band, NOT the
0.4-0.5 band D-136 measured the 0.7451 AUC on. Fitting the cutoff on the same
sample that justified building the feature would be the selection bias this
repo keeps catching elsewhere. Of the three bands D-136 replicated on, 0.3-0.4
is the only one fully disjoint from the discovery band (0.5-0.6 and 0.5-1.01
overlap each other) and it has the largest n.

Statistic choice: the MEDIAN, stated rather than defaulted into. A median split
needs no second tuning parameter and is symmetric -- any other percentile would
itself be a fitted choice requiring its own justification.

Usage:  uv run python scripts/measure_pmi_threshold.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from experiment_lag_matrix import split_at_boundary, standardise  # noqa: E402

from lagmatrix.comovement import _IMPLAUSIBLE_RETURN_CUTOFF  # noqa: E402
from lagmatrix.config import load_settings  # noqa: E402

BAND = (0.3, 0.4)
MIN_EDGED = 20          # halt below this: too few to trust a median


def main() -> None:
    settings = load_settings()
    try:
        from arango import ArangoClient

        db = ArangoClient(hosts=settings.arango_url).db(
            settings.arango_db,
            username=settings.arango_user,
            password=Path("~/.lagmatrix-arango-pw").expanduser().read_text().strip(),
        )
        pmi: dict[tuple[str, str], float] = {}
        for r in db.aql.execute(
            "FOR e IN co_mentioned RETURN {a: PARSE_IDENTIFIER(e._from).key, "
            "b: PARSE_IDENTIFIER(e._to).key, p: e.pmi}"
        ):
            pmi[(r["a"], r["b"])] = pmi[(r["b"], r["a"])] = float(r["p"])
    except Exception as e:                                   # noqa: BLE001
        sys.exit(f"HALT: co_mentioned unreachable at {settings.arango_url}: {e}")
    if not pmi:
        sys.exit("HALT: co_mentioned is empty")

    bars_path = Path("data/bars-10y.parquet")
    if not bars_path.exists():
        sys.exit(f"HALT: {bars_path} not found")
    bars = pd.read_parquet(bars_path)
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change().iloc[1:]
    rets = rets.where(rets.abs() <= _IMPLAUSIBLE_RETURN_CUTOFF)      # D-100
    d_raw, _ = split_at_boundary(rets)
    zd, cols = standardise(d_raw)
    corr = (zd.T @ zd) / len(zd)
    iu, ju = np.triu_indices(len(cols), k=1)
    mag = np.abs(corr[iu, ju])
    sym = np.array(cols)

    lo, hi = BAND
    band = (mag >= lo) & (mag < hi)
    vals = [
        pmi[(sym[i], sym[j])]
        for i, j in zip(iu[band], ju[band], strict=True)
        if (sym[i], sym[j]) in pmi
    ]
    print(f"band {lo}-{hi} (disjoint from D-136's 0.4-0.5 discovery band)")
    print(f"  pairs in band        : {int(band.sum()):,}")
    print(f"  with co-mention edge : {len(vals)}")
    if len(vals) < MIN_EDGED:
        sys.exit(f"HALT: only {len(vals)} edged pairs, below the {MIN_EDGED} "
                 "needed to trust a median (raise as an open question)")
    med = float(np.median(vals))
    print(f"  PMI range            : {min(vals):+.4f} .. {max(vals):+.4f}")
    print(f"  MEDIAN PMI           : {med:.4f}   <- PMI_STRONG_THRESHOLD")


if __name__ == "__main__":
    main()
