"""Terminal node: emit assessments downstream."""

from __future__ import annotations

from lagmatrix.graph.state import LagMatrixState


def publish(state: LagMatrixState) -> dict:
    for a in state.get("assessments", []):
        print(f"[{a.candidate.as_of}] {a.candidate.symbol} {a.candidate.direction} "
              f"-> {a.verdict} (effective evidence {a.effective_evidence:.2f})")
    return {}
