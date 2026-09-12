"""RED for PHASE-5 (PLAN-2026-09-11-quant-daytrade-perspectives): `analyse_day_trade`.

Same gather-node shape as PHASE-4's `analyse_quant` (see `test_quant_analyst.py`):
reached only by a plain `add_edge` from `day_trade_perspective` (a `Send`
target), so it runs once per graph invocation, receiving the fully-merged
`state["day_trade_by_key"]` for every candidate that reached
`day_trade_perspective` in the run, and `state["candidates"]`.

`analyse_day_trade` and `DAY_TRADE_SYSTEM_PROMPT` do not exist yet (TASK-5.4
belongs to the green phase). TASK-5.1/5.2/5.3/5.4 mirror TASK-4.1/4.2/4.3/4.5
exactly, substituting `DayTradePerspective`/`DayTradeAnalystNote` for
`QuantPerspective`/`QuantAnalystNote` and `day_trade_by_key` for
`quant_by_key`.

TASK-5.5 is the task that matters most in this phase: the brief must carry
`median_dollar_vol`, `median_trade_count` and `gap_ratio` literally, *and*
every entry of `not_measurable` with its reason, verbatim -- the one place in
this plan that directly enforces "the model is told what it cannot know."
Day-trade is also the perspective most likely to *read* as validated even
when it silently omits execution cost, so TASK-5.5 is tested on the
fully-populated happy path (every liquidity field present), not only the
missing-data case, and a separate test pins that no fabricated spread/
slippage number ever appears next to the (numberless) reason PHASE-2 gives
for why those are unmeasurable.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from langgraph.runtime import Runtime

from lagmatrix.domain.models import Candidate, DayTradeAnalystNote, DayTradePerspective
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.day_trade_perspective import analyse_day_trade
from lagmatrix.graph.state import candidate_key

_NOT_MEASURABLE = [
    {"kind": "spread", "reason": "no bid/ask quote data in this pipeline"},
    {"kind": "slippage", "reason": "no fill/execution data in this pipeline"},
    {"kind": "borrow", "reason": "no borrow-rate data in this pipeline"},
]


def _candidate(
    symbol: str, as_of: date = date(2026, 6, 1), origin_sigma: float | None = None
) -> Candidate:
    return Candidate(
        symbol=symbol, direction="up", as_of=as_of, origin="scan", origin_sigma=origin_sigma
    )


def _dtp(**overrides) -> DayTradePerspective:
    """A fully-populated happy-path `DayTradePerspective` -- every liquidity
    field present, not the missing-data branch. TASK-5.5's dangerous case:
    a brief full of real numbers is exactly where a silently-dropped
    `not_measurable` entry would read as "execution cost was considered and
    is fine"."""
    defaults = dict(
        median_dollar_vol=1_234_567.0,
        median_trade_count=45_678.0,
        n_sessions=10,
        gap_ratio=0.35,
        not_measurable=[dict(e) for e in _NOT_MEASURABLE],
        note="10 trailing session(s) over a 60-session window",
    )
    defaults.update(overrides)
    return DayTradePerspective(**defaults)


def _runtime(llm, max_llm_candidates: int = 20) -> Runtime[LagMatrixContext]:
    return Runtime(
        context=LagMatrixContext(
            closes=pd.DataFrame(),
            signal_universe=set(),
            llm=llm,
            max_llm_candidates=max_llm_candidates,
        )
    )


# ---------------------------------------------------------------------------
# TASK-5.1 -- one note per candidate that reached day_trade_perspective
# ---------------------------------------------------------------------------


async def test_one_day_trade_analyst_note_per_candidate_that_reached_day_trade_perspective(
    fake_analyst_client,
):
    """TASK-5.1 (mirrors TASK-4.1): with a fake client returning one canned
    note per key, `analyse_day_trade` returns
    `{"day_trade_analyst_by_key": {...}}` with exactly one entry per
    candidate present in `state["day_trade_by_key"]`.

    Falsifiable by: returning fewer entries than candidates, more (a stray
    key not asked for), or entries that are not the fake client's notes
    (e.g. always `status="not_run"` even though an LLM is configured).
    """
    candidates = [_candidate("AAA"), _candidate("BBB"), _candidate("CCC")]
    keys = [candidate_key(c) for c in candidates]
    day_trade_by_key = {k: _dtp() for k in keys}
    responses = {
        k: dict(
            status="ok",
            liquidity_tier="ample",
            gap_dominant=False,
            reasoning=f"canned note for {k}",
            model="fake-model",
        )
        for k in keys
    }
    client = fake_analyst_client(responses=responses)
    state = {"candidates": candidates, "day_trade_by_key": day_trade_by_key}

    out = await analyse_day_trade(state, _runtime(client))

    assert set(out["day_trade_analyst_by_key"]) == set(keys)
    for k in keys:
        note = out["day_trade_analyst_by_key"][k]
        assert isinstance(note, DayTradeAnalystNote)
        assert note.status == "ok"
        assert note.reasoning == f"canned note for {k}"
    assert len(client.calls) == 1
    assert client.calls[0]["schema"] is DayTradeAnalystNote
    assert set(client.calls[0]["briefs"]) == set(keys)


# ---------------------------------------------------------------------------
# TASK-5.2 -- no LLM configured
# ---------------------------------------------------------------------------


async def test_no_llm_configured_gives_every_candidate_a_fully_formed_not_run_note():
    """TASK-5.2 (mirrors TASK-4.2): `runtime.context.llm is None` -- every
    candidate that reached `day_trade_perspective` gets a fully-formed
    `DayTradeAnalystNote(status="not_run", reasoning="no LLM configured",
    ...)`. Not a missing key, not an empty dict entry.

    Falsifiable by: omitting a candidate's key entirely, returning `None` or
    a bare dict instead of a `DayTradeAnalystNote`, or returning a different
    `status`/`reasoning`.
    """
    candidates = [_candidate("AAA"), _candidate("BBB")]
    keys = [candidate_key(c) for c in candidates]
    day_trade_by_key = {k: _dtp() for k in keys}
    state = {"candidates": candidates, "day_trade_by_key": day_trade_by_key}

    out = await analyse_day_trade(state, _runtime(llm=None))

    assert set(out["day_trade_analyst_by_key"]) == set(keys)
    for k in keys:
        note = out["day_trade_analyst_by_key"][k]
        assert isinstance(note, DayTradeAnalystNote)
        assert note.status == "not_run"
        assert note.reasoning == "no LLM configured"
        assert note.liquidity_tier is None
        assert note.gap_dominant is None


# ---------------------------------------------------------------------------
# TASK-5.3 -- more candidates than max_llm_candidates
# ---------------------------------------------------------------------------


async def test_candidates_beyond_the_cap_are_not_run_and_never_sent_to_the_llm(
    fake_analyst_client,
):
    """TASK-5.3/D-130 (mirrors TASK-4.3): with more candidates than
    `runtime.context.max_llm_candidates`, the excess get `status="not_run"`
    with a reason naming the cap, and are never included in what is sent to
    the LLM at all -- the retained ones are the largest-|`origin_sigma`| via
    `cap_for_llm`, not an arbitrary N. No candidate is missing from the
    result either way (D-38).

    Falsifiable by: retaining an arbitrary/alphabetical subset instead of
    the largest-|sigma| two, sending a capped candidate's brief to the fake
    client, or dropping a capped candidate from the result entirely.
    """
    candidates = [
        _candidate("A", origin_sigma=-2.0),
        _candidate("B", origin_sigma=5.0),
        _candidate("C", origin_sigma=1.0),
        _candidate("D", origin_sigma=-4.0),
    ]
    keys = {c.symbol: candidate_key(c) for c in candidates}
    day_trade_by_key = {k: _dtp() for k in keys.values()}
    responses = {
        keys[sym]: dict(
            status="ok",
            liquidity_tier="ample",
            gap_dominant=False,
            reasoning=f"canned note for {sym}",
            model="fake-model",
        )
        for sym in ("B", "D")
    }
    client = fake_analyst_client(responses=responses)
    state = {"candidates": candidates, "day_trade_by_key": day_trade_by_key}

    out = await analyse_day_trade(state, _runtime(client, max_llm_candidates=2))

    result = out["day_trade_analyst_by_key"]
    assert set(result) == set(keys.values()), "no candidate may be missing (D-38)"

    # the two largest-|origin_sigma| candidates (B: 5.0, D: 4.0) got real notes
    assert result[keys["B"]].status == "ok"
    assert result[keys["D"]].status == "ok"

    # the two smallest-|origin_sigma| candidates (C: 1.0, A: 2.0) were capped
    for sym in ("A", "C"):
        note = result[keys[sym]]
        assert isinstance(note, DayTradeAnalystNote)
        assert note.status == "not_run"
        assert "batch cap reached" in note.reasoning
        assert "2" in note.reasoning, "the reason must name the cap (2)"

    # and never reached the fake client at all
    assert len(client.calls) == 1
    assert set(client.calls[0]["briefs"]) == {keys["B"], keys["D"]}


# ---------------------------------------------------------------------------
# TASK-5.5 -- the brief carries the liquidity numbers literally, and every
# not_measurable entry with its reason, verbatim
# ---------------------------------------------------------------------------


async def test_brief_contains_liquidity_numbers_literally(fake_analyst_client):
    """TASK-5.5: the per-candidate brief handed to the LLM contains
    `median_dollar_vol`, `median_trade_count` and `gap_ratio` as literal
    numbers -- the model is handed the numbers PHASE-2 already computed, not
    prose to re-derive them from.

    Falsifiable by: omitting any of the three numbers from the brief, or
    rendering them in a form that doesn't literally contain the value (e.g.
    only a qualitative bucket like "thin").
    """
    candidate = _candidate("AAA")
    key = candidate_key(candidate)
    dtp = _dtp(median_dollar_vol=987_654.0, median_trade_count=12_345.0, gap_ratio=0.42)
    client = fake_analyst_client(
        default=dict(
            status="ok", liquidity_tier="ample", gap_dominant=False,
            reasoning="canned", model="fake-model",
        )
    )
    state = {"candidates": [candidate], "day_trade_by_key": {key: dtp}}

    await analyse_day_trade(state, _runtime(client))

    brief = client.calls[0]["briefs"][key]
    assert str(dtp.median_dollar_vol) in brief
    assert str(dtp.median_trade_count) in brief
    assert str(dtp.gap_ratio) in brief


async def test_brief_states_every_not_measurable_kind_with_its_reason_verbatim_on_the_happy_path(
    fake_analyst_client,
):
    """TASK-5.5, the task that matters most in this phase: the brief must
    name all three unmeasurable execution-cost kinds -- spread, slippage,
    borrow -- each with its own reason string present *verbatim*, not just
    the kind names. This is asserted on the fully-populated happy path (all
    liquidity fields present, via `_dtp()`'s defaults), because that is the
    dangerous case: a brief full of real dollar-volume and trade-count
    numbers is exactly where a silently-dropped execution-cost gap would
    read as "considered and fine" rather than "unknown."

    Falsifiable by: a brief that lists only the kind names ("spread,
    slippage, borrow") without their reasons, that omits any one of the
    three kinds, or that mentions `not_measurable` only in the missing-data
    branch and drops it here.
    """
    candidate = _candidate("AAA")
    key = candidate_key(candidate)
    dtp = _dtp()  # happy path: every liquidity field populated
    client = fake_analyst_client(
        default=dict(
            status="ok", liquidity_tier="ample", gap_dominant=False,
            reasoning="canned", model="fake-model",
        )
    )
    state = {"candidates": [candidate], "day_trade_by_key": {key: dtp}}

    await analyse_day_trade(state, _runtime(client))

    brief = client.calls[0]["briefs"][key]
    for entry in _NOT_MEASURABLE:
        assert entry["kind"] in brief, f"{entry['kind']!r} kind missing from brief"
        assert entry["reason"] in brief, (
            f"reason for {entry['kind']!r} must appear verbatim, not just the kind name"
        )


async def test_brief_states_not_measurable_without_a_fabricated_spread_or_slippage_value(
    fake_analyst_client,
):
    """TASK-5.5's disconfirming case: PHASE-2 emits no spread or slippage
    *number* -- `DayTradePerspective` has no such field, and the plan's halt
    conditions forbid deriving one from `high - low`. A brief that states
    the (numberless) reason for each is correct; a brief that appended a
    fabricated estimate next to it would be worse than silence, because it
    would look measured.

    Falsifiable by: an implementation that renders a computed figure (e.g.
    an average `high - low` or a percentage) alongside the spread/slippage
    line -- the digit check below would then fail.
    """
    candidate = _candidate("AAA")
    key = candidate_key(candidate)
    dtp = _dtp()
    client = fake_analyst_client(
        default=dict(
            status="ok", liquidity_tier="ample", gap_dominant=False,
            reasoning="canned", model="fake-model",
        )
    )
    state = {"candidates": [candidate], "day_trade_by_key": {key: dtp}}

    await analyse_day_trade(state, _runtime(client))

    brief = client.calls[0]["briefs"][key]
    for line in brief.splitlines():
        lowered = line.lower()
        if "spread" in lowered or "slippage" in lowered:
            assert not any(ch.isdigit() for ch in line), (
                f"line mentioning spread/slippage must carry no fabricated number: {line!r}"
            )
