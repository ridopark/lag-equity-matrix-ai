"""Human confirmation gate for a contradicted verdict (D-19, D-35 item 6).

Sits between `assessor` and `publisher`, not inside `assess`: `interrupt()`
raises, so a node that interrupts produces no writes of its own, and the
assessments must already be committed to state for a human to review. This
node must stay pure — LangGraph re-executes an interrupted node from the top
on resume, and `assess`'s `datetime.now(UTC)` stamp is exactly the kind of
non-determinism that would diverge across that replay.
"""

from __future__ import annotations

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState


def review(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    if not runtime.context.halt_on_contradicted:
        return {"approved": True}
    bad = [a for a in state.get("assessments", []) if a.verdict == "contradicted"]
    if not bad:
        return {"approved": True}
    answer = interrupt({
        "reason": "contradicted verdicts require confirmation (D-19)",
        "contradicted": [
            {"symbol": a.candidate.symbol, "as_of": str(a.candidate.as_of),
             "rationale": a.rationale} for a in bad
        ],
    })
    return {"approved": bool(answer)}
