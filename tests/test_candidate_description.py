"""PHASE-2 (D-87): `Assessment.description` flows through end to end;
`rank_by_room` is gone.

TASK-2.1 per `docs/plans/PLAN-2026-09-09-remove-room.md`. Replaces
`tests/test_lag_response.py` (deleted whole -- all 7 of its tests were built
on `origin_status`/`room`/`rank_by_room`, none of which survive D-87).

The workaround that file used no longer applies, and here is why: it hand-built
a `lag_edges` list naming a placeholder leader ("OTHERLEAD") absent from
`closes`, purely to dodge a collision -- in a two-column Y/X universe, Y was
always picked as X's one correlation neighbour, so Y was counted twice: once
as `origin_leader` (`lag_response` Evidence) and once as an ordinary
`leader_move` Evidence, and the two could partially cancel (Q-40). `description`
emits no `Evidence` at all, so there is nothing left for a `leader_move` unit to
collide with -- the trap is gone by construction, not merely avoided. Every
test below therefore calls `retrieve_neighbourhood` for real, closing the old
file's own caveat that its "end-to-end" claim only held from `leader_state`
onward, not from retrieval.

Trap 1 (Pydantic v2 `extra="ignore"`): two `Assessment`s that differ only in a
field pydantic doesn't know about yet compare equal (`Assessment(...,
description="x") == Assessment(...)` is `True` while `description` isn't a
declared field). Every assertion below reads a named attribute
(`.description`, `.verdict`, `.effective_evidence`), never `==` between whole
`Assessment` objects.

Trap 2 (empty baseline slice): `leader_state` has no short-history guard, so
too few sessions yields an empty baseline slice and no shocks at all, making
assertions pass vacuously. Both fixtures below (`_origin_move_closes`'s 12
sessions with `trail=10`/`move_win=3`, `_shocked_closes`'s 62 sessions with
the default `trail=60`/`move_win=3`) are `tests/test_nodes.py`'s own,
already proven non-empty there.

Fixtures are duplicated verbatim from `tests/test_nodes.py` rather than
imported -- that file is not this file's public seam (same house style
`test_lag_response.py` used for the fixture it duplicated).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from langgraph.runtime import Runtime

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.assessor import assess
from lagmatrix.graph.nodes.context_fusion import fuse_evidence
from lagmatrix.graph.nodes.graph_retriever import retrieve_neighbourhood
from lagmatrix.graph.nodes.leader_state import leader_state


def _origin_move_closes(x_recent: list[float]) -> pd.DataFrame:
    """Verbatim copy of `tests/test_nodes.py`'s `_origin_move_closes`: two
    columns, Y (candidate X's `origin_leader`) and X itself, `trail=10`,
    `move_win=3`, `sigma=2.0`, 12 sessions. Y shocks +0.05/+0.06/+0.04 (an
    unambiguous "up" shock); `x_recent` controls X's own last 3 sessions.
    `retrieve_neighbourhood` always picks Y as X's one correlation neighbour
    in this two-column universe -- see that file's docstring for the full
    session-count rationale, unchanged here.
    """
    quiet_y = [0.001, -0.001, 0.0015, -0.0005, 0.001, -0.0015, 0.0005]
    quiet_x = [0.0009, -0.0011, 0.0013, -0.0006, 0.0011, -0.0016, 0.0004]
    y = quiet_y + [0.05, 0.06, 0.04]
    x = quiet_x + list(x_recent)
    idx = pd.bdate_range("2026-01-01", periods=12, tz="UTC")
    r = {"Y": [0.0, *y, 0.0], "X": [0.0, *x, 0.0]}
    return pd.DataFrame(
        {k: 100 * np.cumprod([1 + v for v in vals]) for k, vals in r.items()}, index=idx
    )


def _shocked_closes() -> pd.DataFrame:
    """Verbatim copy of `tests/test_nodes.py`'s `_shocked_closes`: CAND's two
    real neighbours (LEAD1, LEAD2) shock upward over the trailing default
    `trail=60`/`move_win=3` window while CAND itself stays quiet -- a genuine
    corroborating neighbourhood with no `origin_leader` involved at all.
    """
    rng = np.random.default_rng(0)
    n_quiet = 57
    common = rng.normal(0, 0.01, n_quiet)
    lead1_q = common + rng.normal(0, 0.001, n_quiet)
    lead2_q = common + rng.normal(0, 0.001, n_quiet)
    cand_q = common * 0.8 + rng.normal(0, 0.004, n_quiet)
    shock = [0.05, 0.06, 0.04]
    lead1 = list(lead1_q) + shock
    lead2 = list(lead2_q) + [x * 0.95 for x in shock]
    cand = list(cand_q) + [0.0005, 0.0004, 0.0003]

    idx = pd.bdate_range("2026-01-01", periods=62, tz="UTC")
    r = {"LEAD1": [0.0, *lead1, 0.0], "LEAD2": [0.0, *lead2, 0.0], "CAND": [0.0, *cand, 0.0]}
    return pd.DataFrame({k: 100 * np.cumprod([1 + x for x in v]) for k, v in r.items()}, index=idx)


def _run_full_chain(closes: pd.DataFrame, cand: Candidate, **context_kwargs) -> dict:
    """The genuine, un-shortcut chain: `retrieve_neighbourhood` ->
    `leader_state` -> `fuse_evidence` -> `assess`. Returns the `assess()`
    output dict (`out["assessments"][0]` is this candidate's `Assessment`)."""
    rt = Runtime(
        context=LagMatrixContext(closes=closes, signal_universe=set(), **context_kwargs)
    )
    edges = retrieve_neighbourhood({"candidates": [cand]}, rt)["lag_edges"]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]
    fused = fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)
    return assess({"candidates": [cand], **fused})


def test_description_flows_through_to_assessment_without_affecting_verdict():
    """`description` reaches `Assessment` unchanged, and contributes nothing
    to the vote: `_origin_move_closes([0.0, 0.0, 0.0])` (X quiet, Y shocked
    toward the "up" thesis) run through the real end-to-end chain produces
    exactly the ordinary `leader_move` unit's evidence -- Y is X's only
    neighbour, `supports=True`, `weight=1.0` -- so `effective_evidence` is
    exactly `1.0` and `verdict` is `"corroborated"`, the same result the
    correlation vote alone would give with no `description` mechanism at
    all.

    Falsifies if: `a.description` is `None` or omits `"Y"` (the mechanism
    isn't wired to `Assessment`), or if `a.effective_evidence` is anything
    other than exactly `1.0`, or `a.verdict != "corroborated"` (either would
    mean `description` is adding weight or votes of its own -- the thing
    D-87 says it must never do).
    """
    closes = _origin_move_closes([0.0, 0.0, 0.0])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )

    out = _run_full_chain(closes, cand, trail=10, topk=20, move_win=3, sigma=2.0)
    a = out["assessments"][0]

    assert a.description is not None
    assert "Y" in a.description
    assert a.verdict == "corroborated"
    assert a.effective_evidence == 1.0


def test_description_reflects_a_move_against_the_thesis_end_to_end():
    """Sign branch, end to end: X moves against the "up" thesis
    (`_origin_move_closes([-0.05, -0.06, -0.04])`) while Y still shocks
    toward it, so the description must say "against". `a.verdict` is not
    asserted here -- it is driven entirely by the ordinary, unrelated
    `leader_move` unit for Y, which is this test's non-concern (Q-40's
    double-counting question is moot now that `description` emits no
    `Evidence` to collide with).

    Falsifies if: `a.description` does not contain `"against"`.
    """
    closes = _origin_move_closes([-0.05, -0.06, -0.04])
    cand = Candidate(
        symbol="X", direction="up", as_of=date(2026, 1, 15), origin="scan", origin_leader="Y"
    )

    out = _run_full_chain(closes, cand, trail=10, topk=20, move_win=3, sigma=2.0)
    a = out["assessments"][0]

    assert a.description is not None
    assert "against" in a.description


def test_description_is_none_for_corroboration_mode_candidates():
    """Corroboration-mode invariance, end to end: an `origin="external"`
    candidate never sets `origin_leader` (defaults to `None`), so the
    `description` mechanism must be a complete no-op for it, all the way
    through to `Assessment` -- `_shocked_closes()` gives this candidate a
    genuine corroborating neighbourhood with no origin leader involved.

    Falsifies if: `a.description` is not `None` (e.g. `None` matched against
    a literal column/string somewhere in the chain).
    """
    closes = _shocked_closes()
    cand = Candidate(symbol="CAND", direction="up", as_of=date(2026, 3, 26), origin="external")

    out = _run_full_chain(closes, cand)
    a = out["assessments"][0]

    assert a.description is None
