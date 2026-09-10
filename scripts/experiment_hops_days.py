"""D-92: does a shock propagate along the supply chain with a hop-dependent delay?

Run exactly as pre-registered in docs/spikes/overall.md (D-92, commit d091ec4),
written before this script existed. Read-only: seeds nothing, writes nothing to
ArangoDB.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "scripts")
import serve  # noqa: E402

from lagmatrix import shocks as S  # noqa: E402

TRAIL, MW, SIG = 60, 3, 2.0
HORIZONS = [1, 2, 3, 5, 10]
LATE = {5, 10}
THRESHOLD = 0.25          # D-92, economic, declared in advance
serve.ALLOW_REAL = True


def hop_map(db) -> dict[str, dict[str, tuple[int, object]]]:
    """{leader: {follower: (hop, earliest filing date on the path)}}"""
    rows = list(db.aql.execute("""
    FOR e IN supplies_to
      RETURN {sup: PARSE_IDENTIFIER(e._from).key,
              cust: PARSE_IDENTIFIER(e._to).key, filed: e.filing_date}"""))
    e = pd.DataFrame(rows)
    e["filed"] = pd.to_datetime(e.filed).dt.date
    by_cust: dict[str, list] = {}
    for r in e.itertuples():
        by_cust.setdefault(r.cust, []).append((r.sup, r.filed))
    out: dict[str, dict[str, tuple[int, object]]] = {}
    for lead, sups in by_cust.items():
        m: dict[str, tuple[int, object]] = {}
        for s, f in sups:
            m[s] = (1, f)
        for s, f in sups:                      # hop 2: suppliers of suppliers
            for s2, f2 in by_cust.get(s, []):
                if s2 != lead and s2 not in m:
                    m[s2] = (2, max(f, f2))    # path is valid only once BOTH are filed
        out[lead] = m
    return out


def main() -> None:
    db = serve.arango_db()
    if db is None:
        raise SystemExit(f"ArangoDB unavailable: {serve.arango_reason()}")
    hops = hop_map(db)

    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change()
    idx = list(closes.index)
    mkt = rets.mean(axis=1)                    # equal-weighted universe, per D-73/D-74
    vol = rets.rolling(TRAIL).std()

    leaders = [c for c in hops if c in closes.columns]
    recs = []
    for ti in range(TRAIL + MW, len(idx) - max(HORIZONS)):
        if (ti - TRAIL - MW) % MW:             # non-overlapping episode windows
            continue
        base, rec = rets.iloc[ti - MW - TRAIL:ti - MW], rets.iloc[ti - MW:ti]
        cols = [c for c in leaders if rec[c].notna().all() and base[c].notna().all()]
        if not cols:
            continue
        z = S.standardised_moves(rec, cols, MW, base)
        d = idx[ti].date()
        for lead, zl in z.items():
            if abs(zl) < SIG:
                continue
            want = 1.0 if zl > 0 else -1.0
            for foll, (hop, filed) in hops[lead].items():
                if foll not in closes.columns or filed is None or d < filed:
                    continue
                sd = vol[foll].iloc[ti]
                if not np.isfinite(sd) or sd <= 0:
                    continue
                for h in HORIZONS:
                    ex = (rets[foll].iloc[ti:ti + h].sum()
                          - mkt.iloc[ti:ti + h].sum())          # market-excess
                    if not np.isfinite(ex):
                        continue
                    recs.append((d, lead, foll, hop, h, want * ex / (sd * np.sqrt(h))))
    df = pd.DataFrame(recs, columns=["date", "lead", "foll", "hop", "h", "ex"])
    print(f"triples: {len(df):,}   leaders {df.lead.nunique()}   followers {df.foll.nunique()}   "
          f"dates {df.date.nunique()}")
    h1 = df[df.hop == 1].groupby(["lead", "foll"]).ngroups
    h2 = df[df.hop == 2].groupby(["lead", "foll"]).ngroups
    print(f"hop-1 pairs {h1}   hop-2 pairs {h2}")

    print("\ngrid: mean thesis-signed excess (sigma), by hop x horizon")
    grid = df.pivot_table(index="hop", columns="h", values="ex", aggfunc="mean")
    print(grid.round(4).to_string())
    print("\n n per cell")
    print(df.pivot_table(index="hop", columns="h", values="ex", aggfunc="size").to_string())

    d = df[df.h.isin([1, 2] + sorted(LATE))].copy()
    d["hop2"] = (d.hop == 2).astype(float)
    d["late"] = d.h.isin(LATE).astype(float)
    X = np.column_stack([np.ones(len(d)), d.hop2, d.late, d.hop2 * d.late])
    y = d.ex.values
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    r = y - X @ b
    XtXi = np.linalg.inv(X.T @ X)
    M = np.zeros_like(XtXi)
    for _, g in pd.Series(range(len(d))).groupby(d.date.values):
        Xi, ri = X[g.values], r[g.values]
        s = Xi.T @ ri
        M += np.outer(s, s)
    se = np.sqrt(np.diag(XtXi @ M @ XtXi))
    names = ["const", "hop2", "late", "hop2 x late"]
    print("\nPRIMARY (date-clustered):")
    for n_, bi, si in zip(names, b, se, strict=True):
        print(f"  {n_:<12} {bi:+.4f} ({si:.4f})  z={bi/si:+.2f}")
    b3, se3 = b[3], se[3]
    mde = 2.8 * se3
    print(f"\n  realised MDE (2.8*SE) = {mde:.4f} sigma   threshold = {THRESHOLD}")
    print(f"  {'UNDERPOWERED' if mde > THRESHOLD else 'adequately powered'}")

    yr = d.copy()
    yr["year"] = pd.to_datetime(yr.date).dt.year
    ests, ses = [], []
    for _year, g in yr.groupby("year"):
        if g.hop2.nunique() < 2 or len(g) < 50:
            continue
        Xg = np.column_stack([np.ones(len(g)), g.hop2, g.late, g.hop2 * g.late])
        try:
            bg = np.linalg.lstsq(Xg, g.ex.values, rcond=None)[0]
            rg = g.ex.values - Xg @ bg
            sg = np.sqrt(np.linalg.inv(Xg.T @ Xg)[3, 3] * (rg @ rg) / max(len(g) - 4, 1))
            ests.append(bg[3])
            ses.append(sg)
        except np.linalg.LinAlgError:
            continue
    if len(ests) > 1:
        e_, s_ = np.array(ests), np.array(ses)
        w = 1 / s_**2
        pooled = (w * e_).sum() / w.sum()
        Q = (w * (e_ - pooled) ** 2).sum()
        I2 = max(0.0, (Q - (len(e_) - 1)) / Q) * 100 if Q > 0 else 0.0
        print(f"\nSECONDARY: {len(e_)} yearly estimates, Q={Q:.1f}, I^2={I2:.0f}%")
        print("  per-year b3: " + "  ".join(f"{v:+.2f}" for v in e_))
    else:
        I2 = float("nan")
        print("\nSECONDARY: too few yearly estimates")

    ok = (b3 >= THRESHOLD) and (b3 / se3 >= 2) and (I2 < 75)
    print("\n" + "=" * 62)
    print(f"PRE-COMMITTED RULE: b3 >= {THRESHOLD} AND z >= 2 AND I^2 < 75%")
    print(f"  b3 = {b3:+.4f}   z = {b3/se3:+.2f}   I^2 = {I2:.0f}%")
    print(f"  VERDICT: {'PROPAGATION CLAIMED' if ok else 'NULL'}"
          + ("  (but see MDE -- underpowered)" if mde > THRESHOLD else ""))
    print("=" * 62)


if __name__ == "__main__":
    main()
