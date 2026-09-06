"""Weigh the assembled evidence and return a verdict per candidate.

Deterministic. It reports what the neighbourhood shows and must be able to say
`contradicted` (D-19). It deliberately emits **no calibrated probability**: no
powered test has established one (D-34), so `odds_adjustment` stays 0.0 and the
verdict is a description of evidence, not a forecast.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lagmatrix.domain.models import Assessment
from lagmatrix.graph.state import LagMatrixState, candidate_key

MIN_EFFECTIVE = 1.0


def assess(state: LagMatrixState) -> dict:
    evidence_by_key = state.get("evidence_by_key", {})
    effective_by_key = state.get("effective_evidence_by_key", {})
    out: list[Assessment] = []

    for c in state.get("candidates", []):
        key = candidate_key(c)
        if key not in effective_by_key:
            continue  # no neighbourhood reached fusion for this candidate (D-27)
        eff = effective_by_key[key]
        ev = evidence_by_key.get(key, [])
        mine = [e for e in ev if e.kind != "co_mention"]
        pro = [e for e in mine if e.supports]
        con = [e for e in mine if not e.supports]
        w_pro = sum(e.weight for e in pro)
        w_con = sum(e.weight for e in con)

        if eff < MIN_EFFECTIVE or not mine:
            verdict = "neutral"
        elif w_pro > w_con:
            verdict = "corroborated"
        elif w_con > w_pro:
            verdict = "contradicted"
        else:
            verdict = "neutral"

        out.append(
            Assessment(
                candidate=c,
                verdict=verdict,
                odds_adjustment=0.0,  # deliberately uncalibrated — see D-34
                effective_evidence=eff,
                supporting=pro,
                contradicting=con,
                rationale=(
                    f"{len(pro)} corroborating and {len(con)} contradicting neighbour "
                    f"moves, weighted to {eff:.2f} effective independent observations "
                    f"({w_pro:.2f} for / {w_con:.2f} against). Not calibrated: no "
                    f"powered test supports a probability (D-34)."
                ),
                ts=datetime.now(UTC),
            )
        )
    return {"assessments": out}
