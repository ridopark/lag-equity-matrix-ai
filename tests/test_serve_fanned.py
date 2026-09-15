"""RED: `serve.py`'s `FANNED` set has drifted from the graph's real fan-out.

`scripts/serve.py:97` hardcodes

    FANNED = {"graph_retriever", "leader_state", "vector_retriever"}

which `do_GET`'s debug-stream handler (serve.py:288) uses to decide whether a
`task` span event carries the candidate's symbol:

    "key": getattr((pay.get("input") or {}).get("candidate"), "symbol", None)
    if pay.get("name") in FANNED else None

But the graph's actual per-candidate `Send` targets, built in
`src/lagmatrix/graph/builder.py:99` and wired via
`route_on_neighbourhood`'s conditional edge, are

    fan_out = ["leader_state", "quant_perspective", "day_trade_perspective"]
    (+ "vector_retriever" when with_news=True)

`quant_perspective` and `day_trade_perspective` (added for PHASE-6) were never
added to `FANNED`. Observable consequence in the live UI: those two nodes'
Gantt bars render with no candidate label while `leader_state`'s and
`vector_retriever`'s do.

`graph_retriever` itself is the `Send` *source* for `route_on_neighbourhood`,
not one of its targets -- it belongs in `FANNED` for a different reason (it is
the branch that receives the per-candidate `Send` from `fan_out_candidates`),
so this test asserts every per-candidate target is a *subset* of `FANNED`,
not set equality.

The seam: hardcoding the expected target set as a second literal here would
just relocate the same drift risk (a future fan-out node added to the builder
without updating either literal). Instead this derives the true targets from
the compiled graph itself -- `StateGraph.branches["graph_retriever"]
["route_on_neighbourhood"].ends`, the `BranchSpec` LangGraph builds for
`route_on_neighbourhood`'s `add_conditional_edges` call -- so the test still
tells the truth if another perspective node is added to `fan_out` later.
`with_news` is exercised both ways since `vector_retriever` only appears in
the `True` branch.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from lagmatrix.graph.builder import build_graph

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


def _per_candidate_send_targets(*, with_news: bool) -> set[str]:
    """The real fan-out targets of `route_on_neighbourhood`, read off the
    compiled graph rather than re-typed as a literal (see module docstring).
    """
    graph = build_graph(with_news=with_news)
    branch = graph.builder.branches["graph_retriever"]["route_on_neighbourhood"]
    return {end for end in branch.ends if end != "__end__"}


@pytest.mark.parametrize("with_news", [True, False])
def test_fanned_covers_every_per_candidate_send_target(with_news):
    """Every node `route_on_neighbourhood` can `Send` a candidate to must be
    in `FANNED`, so its debug span carries that candidate's symbol.

    Falsifies if: a per-candidate `Send` target (currently
    `quant_perspective` and `day_trade_perspective`) is missing from
    `serve.FANNED`.
    """
    import serve

    targets = _per_candidate_send_targets(with_news=with_news)
    assert targets, "expected at least one per-candidate fan-out target"
    assert targets <= serve.FANNED, targets - serve.FANNED
