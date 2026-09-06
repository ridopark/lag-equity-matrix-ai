"""PHASE-6: `interrupt()` on a contradicted verdict (D-35 item 6 / REQ-6).

Per the plan's design note, `review` sits *after* `assessor`, not inside it:
`interrupt()` raises, so a node that interrupts produces no writes of its own,
and assessments must already be committed to state for a human to review. On
resume, `ainvoke(Command(resume=True), config)` completes the run.

The graph is driven by `ainvoke` throughout (asyncio_mode = auto), matching
the rest of this suite -- `retrieve_news` is async and the checkpointer's
resume path is exercised the same way there.

Fixture verdicts (empirically determined against `closes`, `with_news=False`
-- no news needed for review; verified at both default `topk` and `topk=3`).
`signal_universe={"CAND"}` matters here: `corrwith` never excludes the
candidate's own column on its own, so without excluding it the candidate is
always its own top-correlated "leader" (correlation 1.0), and `leader_state`'s
`or sym == c.symbol` clause then always adds a self-shock that can never
satisfy `abs(cand_z) < abs(z)` against itself -- one guaranteed contradicting
unit regardless of real neighbourhood evidence (Q-26). Production never hits
this: D-27 excludes every alert ticker (i.e. the candidate itself) from the
pool via `signal_universe`. Passing `signal_universe={"CAND"}` here matches
that exclusion so these tests exercise the same path production does, rather
than the self-edge artifact:
- `CAND` @ 2026-03-26, direction "down" -> "contradicted" (CANDD moved >2sigma
  against the "down" thesis).
- `CAND` @ 2026-03-26, direction "up" -> "corroborated" (same move, same
  date, now aligned with the thesis) -- same symbol and date as the
  contradicted case, differing only in `direction`.
"""

from __future__ import annotations

from datetime import date

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext

AS_OF = date(2026, 3, 26)


def _candidate(direction: str) -> Candidate:
    return Candidate(symbol="CAND", direction=direction, as_of=AS_OF, origin="external")


async def test_contradicted_verdict_interrupts(closes):
    """A contradicted verdict, run with `halt_on_contradicted=True`, pauses
    the graph via `interrupt()` -- but only after `assessor` has already
    committed its assessments to state, per the design note (a `review` node
    reading already-written state, not `interrupt()` inside `assess` itself).

    Falsifies if: `out["__interrupt__"]` is absent (review never interrupts,
    or the flag is ignored), the interrupt payload does not name "CAND", or
    `out["assessments"]` is empty/missing -- the last of which is the actual
    point of putting `review` after `assessor` rather than inside it.
    """
    graph = build_graph(with_news=False, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(
        closes=closes, signal_universe={"CAND"}, halt_on_contradicted=True
    )
    config = {"configurable": {"thread_id": "review-interrupt-test"}}

    out = await graph.ainvoke(
        {"candidates": [_candidate("down")]}, context=ctx, config=config
    )

    assert "__interrupt__" in out
    payload = out["__interrupt__"][0].value
    contradicted_symbols = {c["symbol"] for c in payload["contradicted"]}
    assert "CAND" in contradicted_symbols

    assessments = out.get("assessments")
    assert assessments, "assessments must already be committed when the interrupt fires"
    assert {a.candidate.symbol for a in assessments} == {"CAND"}
    assert next(a for a in assessments if a.candidate.symbol == "CAND").verdict == "contradicted"


async def test_resume_after_approval_completes_the_run(closes):
    """Resuming an interrupted run with `Command(resume=True)` completes it:
    `approved` becomes `True` and the assessments computed before the
    interrupt are unchanged (`review` is pure and safe to re-execute from the
    top on resume, per `interrupt`'s documented replay behaviour).

    Falsifies if: the resumed `ainvoke` raises, still carries
    `__interrupt__`, `out["approved"]` is not `True`, or the resumed
    assessments' verdicts differ from what the interrupted run already saw.
    """
    graph = build_graph(with_news=False, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(
        closes=closes, signal_universe={"CAND"}, halt_on_contradicted=True
    )
    config = {"configurable": {"thread_id": "review-resume-test"}}

    interrupted = await graph.ainvoke(
        {"candidates": [_candidate("down")]}, context=ctx, config=config
    )
    assert "__interrupt__" in interrupted
    before = {(a.candidate.symbol, a.verdict) for a in interrupted["assessments"]}

    out = await graph.ainvoke(Command(resume=True), context=ctx, config=config)

    assert "__interrupt__" not in out
    assert out["approved"] is True
    after = {(a.candidate.symbol, a.verdict) for a in out["assessments"]}
    assert after == before


async def test_no_contradicted_verdict_does_not_interrupt(closes):
    """A batch with no contradicted verdict runs straight through even with
    `halt_on_contradicted=True` -- `review` only interrupts when there is
    something for a human to confirm.

    Falsifies if: `out["__interrupt__"]` is present despite every verdict in
    the batch being "corroborated" (or "neutral") -- i.e. `review` interrupts
    unconditionally rather than checking for a contradicted verdict.
    """
    graph = build_graph(with_news=False, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(
        closes=closes, signal_universe={"CAND"}, halt_on_contradicted=True
    )
    config = {"configurable": {"thread_id": "review-no-interrupt-test"}}

    out = await graph.ainvoke(
        {"candidates": [_candidate("up")]}, context=ctx, config=config
    )

    assert "__interrupt__" not in out
    assessments = out["assessments"]
    assert {a.verdict for a in assessments} == {"corroborated"}


async def test_default_path_never_interrupts(closes):
    """With `halt_on_contradicted=False` (the default), a batch containing a
    contradicted verdict still runs to completion without pausing -- this is
    what protects `check_baseline.py`'s unattended, non-interactive run from
    silently wedging if the flag were ever turned on by accident.

    Falsifies if: `out["__interrupt__"]` is present when
    `halt_on_contradicted` is `False` -- i.e. `review` interrupts regardless
    of the flag instead of gating on it.
    """
    graph = build_graph(with_news=False, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(
        closes=closes, signal_universe={"CAND"}, halt_on_contradicted=False
    )
    config = {"configurable": {"thread_id": "review-default-test"}}

    out = await graph.ainvoke(
        {"candidates": [_candidate("down")]}, context=ctx, config=config
    )

    assert "__interrupt__" not in out
    assessments = out["assessments"]
    assert {a.candidate.symbol for a in assessments} == {"CAND"}
    assert next(a for a in assessments if a.candidate.symbol == "CAND").verdict == "contradicted"
