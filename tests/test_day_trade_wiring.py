"""RED for PHASE-6 (PLAN-2026-09-11-quant-daytrade-perspectives): wiring
`day_trade_perspective` -> `day_trade_analyst` -> `assessor` into the real
graph.

PHASE-2 (`compute_day_trade_perspective`) and PHASE-5 (`analyse_day_trade`)
already exist and are unit-tested in isolation
(`test_day_trade_perspective.py`, `test_day_trade_analyst.py`). Nothing yet
calls either of them from `build_graph()` -- `Assessment` has no
`day_trade`/`day_trade_analyst` field, and neither node name appears in the
compiled graph (confirmed directly against
`build_graph(with_news=False).get_graph().nodes` and
`Assessment.model_fields` before writing this file: both come back empty).
These tests pin the wiring itself, not the math PHASE-2/5 already cover.

None of these tests supply `bars` -- `run_graph` has no `bars=` kwarg yet
(see the report to the team lead: `LagMatrixContext` has no `bars` field
either, so threading one through now would either be silently dropped or
crash every existing `run_graph` call). `compute_day_trade_perspective`
already handles `bars=None` gracefully (`test_day_trade_perspective.py`'s
`test_bars_none_yields_liquidity_fields_none_with_a_stated_reason`), and its
`note` field embeds the candidate's own symbol even in that path, which is
enough to test wiring and per-candidate isolation without real bars data.
"""

from __future__ import annotations

from datetime import date

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.state import candidate_key


def _candidate(symbol: str, d: date = date(2026, 6, 1), direction: str = "up") -> Candidate:
    return Candidate(symbol=symbol, direction=direction, as_of=d, origin="external")


def test_no_llm_configured_populates_day_trade_but_leaves_analyst_not_run(run_graph):
    """TASK-6.2 (day-trade side): `llm=None` through the real graph -- the
    deterministic PHASE-2 read still runs (`assessments[0].day_trade`
    populated) while the PHASE-5 gather node reports it never called an LLM
    (`assessments[0].day_trade_analyst.status == "not_run"`), reached
    through the graph rather than called directly as PHASE-5's own tests do.

    Falsifiable by: `day_trade` staying `None`/the attribute being absent
    (the node was never wired or never reached), or `day_trade_analyst`
    being absent, or `day_trade_analyst.status` being anything other than
    `"not_run"`.
    """
    out = run_graph([_candidate("CAND")])
    a = out["assessments"][0]

    assert a.day_trade is not None
    assert a.day_trade_analyst is not None
    assert a.day_trade_analyst.status == "not_run"


def test_injected_llm_produces_an_ok_note_matching_the_canned_response(
    run_graph, fake_analyst_client,
):
    """TASK-6.3 (day-trade side): a fake `AnalystClient` injected via
    `run_graph(llm=...)` -- `assessments[0].day_trade_analyst.status ==
    "ok"` and its `reasoning` matches the fake client's canned note, proving
    the injected client is actually threaded through `day_trade_perspective
    -> day_trade_analyst -> assessor`, not merely accepted and ignored.

    Falsifiable by: `status` staying `"not_run"`/`"error"` despite an `llm`
    being configured, or `reasoning` not matching the canned value (e.g.
    `assess()` hard-codes a note instead of reading
    `day_trade_analyst_by_key`).
    """
    candidate = _candidate("CAND")
    key = candidate_key(candidate)
    client = fake_analyst_client(
        default=dict(
            status="ok",
            liquidity_tier="ample",
            gap_dominant=False,
            reasoning=f"canned day-trade note for {key}",
            model="fake-model",
        )
    )

    out = run_graph([candidate], llm=client)
    a = out["assessments"][0]

    assert a.day_trade_analyst.status == "ok"
    assert a.day_trade_analyst.reasoning == f"canned day-trade note for {key}"


def test_two_candidates_do_not_cross_attribute_day_trade_fields(run_graph, fake_analyst_client):
    """TASK-6.4 -- the halt condition. CAND and CAND2 fanned out together
    with no `bars` configured: each assessment's `day_trade` and
    `day_trade_analyst` must reflect only its own candidate, not the
    other's and not a merge of both (D-89's exact failure class).

    Genuinely discriminating, not coincidentally passing: the fake client
    returns a different canned `reasoning` string per `candidate_key`, and
    the guard assertion below confirms the two candidates' real
    `day_trade.note` values (computed independently of any canned response,
    and embedding each candidate's own symbol per
    `compute_day_trade_perspective`'s `bars=None` path) actually differ
    before trusting the rest.

    Cross-attribution is checked two ways: against the other candidate's
    canned note directly, and against what each candidate produces when run
    alone (mirrors the existing `test_two_candidates_do_not_cross_attribute`
    idiom in `test_fanout.py` for the pre-existing evidence fields).

    Falsifiable by: either assessment's `day_trade_analyst.reasoning`
    containing the OTHER candidate's canned string (a swap), the batched
    run's `day_trade`/`day_trade_analyst` for either candidate differing
    from that same candidate's solo run (a merge/overwrite across the two
    `Send` branches), or the two candidates' real `day_trade.note` values
    coming out equal (which would make a swap undetectable and the rest of
    this test worthless).
    """
    cand, cand2 = _candidate("CAND"), _candidate("CAND2")
    key1, key2 = candidate_key(cand), candidate_key(cand2)
    response1 = dict(
        status="ok", liquidity_tier="ample", gap_dominant=False,
        reasoning=f"day-trade note for {key1}", model="fake-model",
    )
    response2 = dict(
        status="ok", liquidity_tier="thin", gap_dominant=True,
        reasoning=f"day-trade note for {key2}", model="fake-model",
    )
    batched_client = fake_analyst_client(responses={key1: response1, key2: response2})

    batched = run_graph([cand, cand2], llm=batched_client)
    solo1 = run_graph([cand], llm=fake_analyst_client(responses={key1: response1}))
    solo2 = run_graph([cand2], llm=fake_analyst_client(responses={key2: response2}))

    by_key = {candidate_key(a.candidate): a for a in batched["assessments"]}
    a1, a2 = by_key[key1], by_key[key2]
    solo_a1, solo_a2 = solo1["assessments"][0], solo2["assessments"][0]

    # guard: the real day-trade reads must actually differ (each embeds its
    # own symbol even with no bars configured) -- otherwise a swap between
    # the two would be invisible to the assertions below.
    assert a1.day_trade.note != a2.day_trade.note

    assert a1.day_trade_analyst.reasoning == f"day-trade note for {key1}"
    assert a2.day_trade_analyst.reasoning == f"day-trade note for {key2}"
    assert a1.day_trade == solo_a1.day_trade
    assert a2.day_trade == solo_a2.day_trade
    assert a1.day_trade_analyst == solo_a1.day_trade_analyst
    assert a2.day_trade_analyst == solo_a2.day_trade_analyst
