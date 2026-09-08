"""PHASE-4: `assessor.py` carries `origin_status`/`room`; proves the split.

TASK-4.1 (verdict/origin_status/room end-to-end) and TASK-4.2a (`rank_by_room`
ordering), per `docs/plans/PLAN-2026-09-08-unresponded-lag.md`.

Trap 1 (Pydantic v2 `extra="ignore"`): `Assessment` equality is vacuous while
`origin_status`/`room` don't exist as fields yet -- two `Assessment`s that
differ only in those kwargs compare equal. Every assertion below reads a
named attribute (`.origin_status`, `.room`, `.verdict`, `.candidate.symbol`),
never `==` between whole `Assessment` objects.

Trap 2 (empty baseline slice): avoided by reusing `test_nodes.py`'s own
`_lag_response_closes` shape verbatim (`trail=10`, `move_win=3`, 12 sessions,
`as_of` on session 10) -- already proven non-empty there.

A THIRD, newly-verified trap, and the reason every fixture below is built
differently from the plan's literal TASK-4.1 wording ("run through
retrieve_neighbourhood"): a real `retrieve_neighbourhood` call over a
two-column Y/X universe always puts Y in X's own top-k correlation pool
(there is nothing else in the pool to pick), so Y ends up double-counted --
once as `origin_leader` (`lag_response`) and once as an ordinary correlation
`leader_move`. Computed directly against `_lag_response_closes` before
writing any assertion: for x_recent=[0.06,0.07,0.05] (the "responded" case),
the spurious `leader_move` evidence for Y comes out `supports=False` (X has
moved further than Y, so the ordinary "hasn't caught up yet" support test
fails) -- and since `lag_response` emits nothing at all for "responded", that
leftover contradicting `leader_move` alone drives `verdict` to
`"contradicted"`, not `"neutral"`/`!= "contradicted"` as the plan's success
criterion requires. For x_recent=[-0.05,-0.06,-0.04] (the "opposed" case) the
contamination runs the other way: the same ordinary `leader_move` reads
`supports=True` (Y moved toward the thesis and X has not yet "caught up" to
Y's magnitude in the ordinary/unsigned sense -- that test never looks at the
candidate's own sign), which cancels the genuinely-contradicting
`lag_response` unit one-for-one and produces `verdict == "neutral"`, not
`"contradicted"`. This is exactly the PHASE-4 halt condition's named
scenario ("a large X move drags in a correlated bloc... perturbing
correlation-neighbour evidence for unrelated reasons") -- so every fixture
here follows the halt condition's prescribed remedy: reduced to the
two-column, no-other-neighbours shape by constructing `lag_edges` directly
(one edge naming a placeholder leader, "OTHERLEAD", that is not a column in
`closes` at all) rather than calling `retrieve_neighbourhood`, mirroring
`test_nodes.py`'s own `test_leader_state_always_resolves_the_origin_leader_
even_below_sigma`, which already established this exact pattern keeps
`leader_state`'s `if not leaders: continue` guard from skipping the
candidate while contributing no shock (and therefore no `leader_move`
evidence) of its own. Every z/room value below was computed by running this
exact `leader_state` -> `fuse_evidence` -> `assess` chain before writing the
assertions, not assumed; the full-chain (uncorrected) numbers are recorded
here too so the discrepancy is checkable rather than asserted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
from langgraph.runtime import Runtime

from lagmatrix.domain.models import Assessment, Candidate, LagEdge
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes import assessor
from lagmatrix.graph.nodes.assessor import assess
from lagmatrix.graph.nodes.context_fusion import fuse_evidence
from lagmatrix.graph.nodes.leader_state import leader_state

TRAIL = 10
MOVE_WIN = 3
SIGMA = 2.0


def _lag_response_closes(x_recent: list[float]) -> pd.DataFrame:
    """Verbatim copy of `test_nodes.py`'s `_lag_response_closes` (same seed
    data, same session shape) -- duplicated rather than imported because
    `tests/test_nodes.py` is not this file's public seam. See that file's
    docstring for the full session-count rationale; unchanged here.
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


def _run_lag_response_chain(closes: pd.DataFrame, cand: Candidate) -> Assessment:
    """`leader_state` -> `fuse_evidence` -> `assess`, over a hand-built
    single-edge `lag_edges` list naming a leader ("OTHERLEAD") absent from
    `closes` -- see module docstring's third trap for why this replaces a
    real `retrieve_neighbourhood` call. `OTHERLEAD` keeps `leader_state`'s
    `if not leaders: continue` guard from skipping the candidate (`leaders`
    is non-empty) but is filtered out of `syms` before any shock is computed
    (`if s in returns.columns`, and it is not a column at all), so it
    contributes no shock and therefore no `leader_move` Evidence -- `Y`'s
    shock reaches `leader_shocks` only through the PHASE-2 `origin_leader`
    clause, exactly as `test_leader_state_always_resolves_the_origin_leader_
    even_below_sigma` (`tests/test_nodes.py`) already proved for a
    below-threshold Y; here Y is above threshold, but the same isolation
    applies. Returns the single `Assessment` for `cand`.
    """
    rt = Runtime(
        context=LagMatrixContext(
            closes=closes, signal_universe=set(), trail=TRAIL, topk=20,
            move_win=MOVE_WIN, sigma=SIGMA,
        )
    )
    edges = [
        LagEdge(
            leader="OTHERLEAD", lagger="X", correlation=0.5, lag_days=0,
            beta=0.3, relation="correlation",
        )
    ]
    shocks = leader_state({"candidates": [cand], "lag_edges": edges}, rt)["leader_shocks"]
    fused = fuse_evidence({"candidates": [cand], "lag_edges": edges, "leader_shocks": shocks}, rt)
    out = assess({"candidates": [cand], **fused})
    return out["assessments"][0]


def _origin_candidate(direction: str = "up") -> Candidate:
    return Candidate(
        symbol="X", direction=direction, as_of=date(2026, 1, 15), origin="scan",
        origin_leader="Y",
    )


def test_lag_response_unmoved_candidate_is_corroborated_with_full_room():
    """Success Criterion 4, case (a): `X` truly unmoved (`x_component ==
    0.0`, the same "literal zero, not offsetting noise" construction
    `test_nodes.py`'s PHASE-3 test docstring records) -> `origin_status ==
    "open"`, `room == 1.0` exactly, `verdict == "corroborated"`.

    Computed directly (`leader_state` -> `fuse_evidence` -> `assess`, this
    file's isolated construction) against `_lag_response_closes([0.0, 0.0,
    0.0])` before writing this assertion: Y's z (`y_component`, `want=+1`)
    is `3.526436`, X's z is exactly `0.0`, giving `room == round(1 -
    0/3.526436, 4) == 1.0`. The lone Evidence is
    `(kind="lag_response", symbol="Y", supports=True, weight=1.0)`,
    `effective_evidence == 1.0` (clears `assessor.MIN_EFFECTIVE == 1.0` on
    its own, `w_pro=1.0 > w_con=0.0`) -> `"corroborated"`.

    Falsifies if: `.origin_status` or `.room` raise `AttributeError` (the
    fields TASK-4.2 must add to `Assessment`), `room != 1.0`,
    `origin_status != "open"`, or `verdict != "corroborated"`.
    """
    closes = _lag_response_closes([0.0, 0.0, 0.0])
    cand = _origin_candidate()

    a = _run_lag_response_chain(closes, cand)

    assert a.verdict == "corroborated"
    assert a.origin_status == "open"
    assert a.room == 1.0


def test_lag_response_partly_moved_candidate_is_corroborated_with_partial_room():
    """Success Criterion 4, case (b): `X` moved partway in the thesis
    direction (`0 < x_component < y_component`) -> `origin_status ==
    "open"`, `0 < room < 1`, `verdict == "corroborated"`.

    Computed directly against `_lag_response_closes([0.005, 0.006, 0.004])`
    before writing this assertion: `y_component == 3.526436`, `x_component
    == 3.318779`, `room == round(1 - 3.318779/3.526436, 4) == 0.0589` --
    comfortably interior, not a boundary value. Evidence and verdict
    mechanics are otherwise identical to the unmoved case (one
    `supports=True` `lag_response` unit, `effective_evidence == 1.0`,
    `w_pro > w_con`).

    Falsifies if: `room` is `<= 0` or `>= 1`, `origin_status != "open"`, or
    `verdict != "corroborated"`.
    """
    closes = _lag_response_closes([0.005, 0.006, 0.004])
    cand = _origin_candidate()

    a = _run_lag_response_chain(closes, cand)

    assert a.verdict == "corroborated"
    assert a.origin_status == "open"
    assert 0 < a.room < 1


def test_lag_response_already_responded_does_not_read_as_contradicted():
    """Success Criterion 4, case (c) -- the falsifying test the whole
    feature rests on: `X` has moved at least as much as `Y`, same
    direction (`x_component >= y_component`) -> `origin_status ==
    "responded"`, `room == 0.0`, and `verdict != "contradicted"`. If this
    instead read `"contradicted"`, "already responded" would have been
    conflated with "refuted" -- the exact distinction D2/D3 exist to keep
    apart.

    Computed directly against `_lag_response_closes([0.06, 0.07, 0.05])`
    (this file's isolated `leader_state`/`fuse_evidence` construction, see
    module docstring's third trap) before writing this assertion:
    `y_component == 3.526436`, `x_component == 3.540641` -- `x_component >=
    y_component` by a clear margin, landing in "responded". `fuse_evidence`
    emits **no** Evidence at all for "responded" (D2), so `evidence == []`,
    `effective_evidence == 0.0` -- below `assessor.MIN_EFFECTIVE == 1.0` ->
    `verdict == "neutral"`.

    (A real, un-isolated `retrieve_neighbourhood` call over this same
    two-column data does *not* give `"neutral"` here -- see the module
    docstring: Y also lands in X's own correlation pool and contributes a
    spurious `supports=False` `leader_move` unit, which alone would drive
    `verdict` to `"contradicted"`, exactly the outcome this test exists to
    rule out. That is the PHASE-4 halt condition's named scenario, not this
    test failing to isolate the mechanism it targets.)

    Falsifies if: `verdict == "contradicted"`, `origin_status !=
    "responded"`, or `room != 0.0`.
    """
    closes = _lag_response_closes([0.06, 0.07, 0.05])
    cand = _origin_candidate()

    a = _run_lag_response_chain(closes, cand)

    assert a.verdict != "contradicted"
    assert a.origin_status == "responded"
    assert a.room == 0.0


def test_lag_response_opposed_candidate_is_contradicted_with_no_room():
    """Success Criterion 4, case (d): `X` moved against the thesis
    (`x_component < 0`) -> `origin_status == "opposed"`, `room is None`
    (refuted, not merely spent -- no room figure applies), `verdict ==
    "contradicted"`.

    Computed directly against `_lag_response_closes([-0.05, -0.06, -0.04])`
    before writing this assertion: `y_component == 3.526436`, `x_component
    == -3.512844` -- clearly negative. The lone Evidence is
    `(kind="lag_response", symbol="Y", supports=False, weight=1.0)`,
    `effective_evidence == 1.0`, `w_con=1.0 > w_pro=0.0` -> `"contradicted"`.

    Falsifies if: `room` is anything other than `None` (e.g. a numeric
    `0.0`, collapsing "opposed" into "responded"), `origin_status !=
    "opposed"`, or `verdict != "contradicted"`.
    """
    closes = _lag_response_closes([-0.05, -0.06, -0.04])
    cand = _origin_candidate()

    a = _run_lag_response_chain(closes, cand)

    assert a.verdict == "contradicted"
    assert a.origin_status == "opposed"
    assert a.room is None


def test_lag_response_weight_alone_clears_min_effective():
    """TASK-4.1 test 5: with no reachable correlation neighbours at all
    (this file's isolated construction -- `OTHERLEAD` contributes no
    shock), the single `lag_response` Evidence's `weight=1.0` alone must
    clear `assessor.MIN_EFFECTIVE == 1.0` and produce a non-neutral
    verdict -- confirming `lag_response` is a first-class, independently
    sufficient unit of evidence, not merely a tie-breaker riding on other
    evidence.

    Reuses case (a) (`_lag_response_closes([0.0, 0.0, 0.0])`):
    `effective_evidence == 1.0` (only the `lag_response` unit contributes;
    no `leader_move` reaches fusion), which is `>= MIN_EFFECTIVE`, so
    `assess`'s `eff < MIN_EFFECTIVE or not mine` guard does not fire.

    Falsifies if: `verdict == "neutral"` -- meaning either
    `effective_evidence` did not reach `1.0` from `lag_response` alone, or
    `mine` was empty despite the `Evidence` existing.
    """
    closes = _lag_response_closes([0.0, 0.0, 0.0])
    cand = _origin_candidate()

    a = _run_lag_response_chain(closes, cand)

    assert a.verdict != "neutral"


def test_responded_with_no_other_evidence_is_neutral_not_contradicted():
    """TASK-4.1 test 6: with no reachable correlation neighbours (same
    isolated construction) and `X` "responded" (case (c),
    `x_recent=[0.06, 0.07, 0.05]`), `lag_response` emits nothing at all
    (D2), so there is no evidence whatsoever for this candidate ->
    `effective_evidence == 0.0`, `evidence == []` -> `verdict == "neutral"`.
    This proves "responded" cannot manufacture a verdict in *either*
    direction on its own -- not "corroborated" (the thesis is spent, not
    reinforced) and not "contradicted" (D2's core claim).

    Same underlying numbers as `test_lag_response_already_responded_does_
    not_read_as_contradicted` above (both use case (c) with no other
    neighbours) -- that test pins the weaker, general claim
    (`!= "contradicted"`) alongside `origin_status`/`room`; this test pins
    the exact, stronger outcome (`== "neutral"`) as its own falsifiable
    claim.

    Falsifies if: `verdict != "neutral"` (e.g. `"contradicted"`, if a
    stray Evidence unit leaked in from somewhere this construction did not
    intend).
    """
    closes = _lag_response_closes([0.06, 0.07, 0.05])
    cand = _origin_candidate()

    a = _run_lag_response_chain(closes, cand)

    assert a.verdict == "neutral"


# --- TASK-4.2a: rank_by_room orders open (desc room) < responded < opposed < None ---


def _hand_built_assessment(
    symbol: str, origin_status: str | None, room: float | None
) -> Assessment:
    """A minimal `Assessment` for ranking only -- `verdict`/`effective_
    evidence`/`supporting`/`contradicting`/`rationale` are irrelevant to
    `rank_by_room` and held constant across every bucket so only
    `origin_status`/`room` (via `symbol`, for identification) can drive
    ordering differences. Distinguished by `candidate.symbol`, never by
    `Assessment` equality (Trap 1 -- see module docstring)."""
    cand = Candidate(
        symbol=symbol, direction="up", as_of=date(2026, 1, 15),
        origin="scan" if origin_status is not None else "external",
        origin_leader="Y" if origin_status is not None else None,
    )
    return Assessment(
        candidate=cand,
        verdict="neutral",
        odds_adjustment=0.0,
        effective_evidence=1.0,
        supporting=[],
        contradicting=[],
        rationale="test fixture",
        ts=datetime.now(UTC),
        origin_status=origin_status,
        room=room,
    )


def test_rank_by_room_orders_open_descending_then_responded_then_opposed_then_none():
    """TASK-4.2/Success Criterion 5: `rank_by_room` sorts by `(priority,
    -(room or 0.0))` with `priority = {"open": 0, "responded": 1,
    "opposed": 2, None: 3}`. Five hand-built `Assessment`s, one per bucket
    plus a second "open" at a different `room`, fed in scrambled order;
    asserts the exact expected symbol order out of `assessor.rank_by_room`
    -- identity via `candidate.symbol`, never whole-`Assessment` equality
    (Trap 1).

    Falsifies if: `assessor.rank_by_room` does not exist (today's state --
    `AttributeError`), or once it exists, the returned order is anything
    other than `["OPEN_HI", "OPEN_LO", "RESP", "OPP", "NONE_STATUS"]` --
    e.g. the two "open" entries swapped (secondary key wrong or absent),
    or any bucket landing out of `open < responded < opposed < None` order.
    """
    open_hi = _hand_built_assessment("OPEN_HI", "open", 0.8)
    open_lo = _hand_built_assessment("OPEN_LO", "open", 0.3)
    responded = _hand_built_assessment("RESP", "responded", 0.0)
    opposed = _hand_built_assessment("OPP", "opposed", None)
    none_status = _hand_built_assessment("NONE_STATUS", None, None)

    ranked = assessor.rank_by_room([opposed, none_status, responded, open_lo, open_hi])

    assert [a.candidate.symbol for a in ranked] == [
        "OPEN_HI", "OPEN_LO", "RESP", "OPP", "NONE_STATUS",
    ]
