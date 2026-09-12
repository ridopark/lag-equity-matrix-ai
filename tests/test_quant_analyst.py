"""RED for PHASE-4 (PLAN-2026-09-11-quant-daytrade-perspectives): `analyse_quant`.

`analyse_quant` is a gather node, not a per-candidate one (the correction in
this plan revision): reached only by a plain `add_edge` from `quant_perspective`
(a `Send` target), so it runs once per graph invocation, receiving the
fully-merged `state["quant_by_key"]` for every candidate that reached
`quant_perspective` in the run, and `state["candidates"]` -- exactly the shape
`context_fusion` already has downstream of `leader_state`.

`analyse_quant` and `QUANT_SYSTEM_PROMPT` do not exist yet (TASK-4.5 belongs to
the green phase). These tests pin four things the green implementation must
satisfy:

* one `QuantAnalystNote` per candidate that reached `quant_perspective`, via
  the injected fake `AnalystClient` (TASK-4.1);
* `runtime.context.llm is None` produces a fully-formed `status="not_run"`
  note for every candidate -- never a missing key (TASK-4.2);
* more candidates than `runtime.context.max_llm_candidates` -- the excess are
  never sent to the LLM at all and come back `status="not_run"` naming the
  cap; the retained ones are the largest-|`origin_sigma`| via TASK-3.12's
  `cap_for_llm`, not an arbitrary N (TASK-4.3, D-130);
* the brief handed to the LLM contains each candidate's CI width, duplicate
  count, split-half percentage and ETF flag literally, and states that
  `median_ci_width` is a lower bound, not a true interval -- `LagEdge` carries
  no per-edge session count, so PHASE-1 passed `trail` (an upper bound on
  valid sessions) to `confidence_interval`, while `comovement_edges` uses the
  real pairwise valid-session count (D-100 masks implausible returns) when it
  built the correlation in the first place. The interval PHASE-1 computed is
  therefore systematically narrower than the truth, and the model must not be
  left to reason from it as if it weren't (TASK-4.4, and the team lead's
  addition to that task).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from langgraph.runtime import Runtime

from lagmatrix.domain.models import Candidate, QuantAnalystNote, QuantPerspective
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.quant_perspective import analyse_quant
from lagmatrix.graph.state import candidate_key


def _candidate(
    symbol: str, as_of: date = date(2026, 6, 1), origin_sigma: float | None = None
) -> Candidate:
    return Candidate(
        symbol=symbol, direction="up", as_of=as_of, origin="scan", origin_sigma=origin_sigma
    )


def _qp(**overrides) -> QuantPerspective:
    defaults = dict(
        n_edges=4,
        median_ci_width=0.246,
        duplicate_count=1,
        split_half_sign_agree_pct=75.0,
        candidate_is_etf=False,
        note="4 correlation edge(s) over a 60-session trailing window",
    )
    defaults.update(overrides)
    return QuantPerspective(**defaults)


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
# TASK-4.1 -- one note per candidate that reached quant_perspective
# ---------------------------------------------------------------------------


async def test_one_quant_analyst_note_per_candidate_that_reached_quant_perspective(
    fake_analyst_client,
):
    """TASK-4.1: with a fake client returning one canned note per key,
    `analyse_quant` returns `{"quant_analyst_by_key": {...}}` with exactly one
    entry per candidate present in `state["quant_by_key"]`.

    Falsifiable by: returning fewer entries than candidates (e.g. only the
    first), more (a stray key not asked for), or entries that are not the
    fake client's notes (e.g. always `status="not_run"` even though an LLM
    is configured).
    """
    candidates = [_candidate("AAA"), _candidate("BBB"), _candidate("CCC")]
    keys = [candidate_key(c) for c in candidates]
    quant_by_key = {k: _qp() for k in keys}
    responses = {
        k: dict(
            status="ok",
            replication_expectation="high",
            flagged_concerns=[],
            reasoning=f"canned note for {k}",
            model="fake-model",
        )
        for k in keys
    }
    client = fake_analyst_client(responses=responses)
    state = {"candidates": candidates, "quant_by_key": quant_by_key}

    out = await analyse_quant(state, _runtime(client))

    assert set(out["quant_analyst_by_key"]) == set(keys)
    for k in keys:
        note = out["quant_analyst_by_key"][k]
        assert isinstance(note, QuantAnalystNote)
        assert note.status == "ok"
        assert note.reasoning == f"canned note for {k}"
    assert len(client.calls) == 1
    assert client.calls[0]["schema"] is QuantAnalystNote
    assert set(client.calls[0]["briefs"]) == set(keys)


# ---------------------------------------------------------------------------
# TASK-4.2 -- no LLM configured
# ---------------------------------------------------------------------------


async def test_no_llm_configured_gives_every_candidate_a_fully_formed_not_run_note():
    """TASK-4.2: `runtime.context.llm is None` -- every candidate that reached
    `quant_perspective` gets a fully-formed `QuantAnalystNote(status="not_run",
    reasoning="no LLM configured", ...)`. Not a missing key, not an empty
    dict entry -- a real note, so a downstream reader never has to special-
    case "absent" versus "not run".

    Falsifiable by: omitting a candidate's key entirely, returning `None` or
    a bare dict instead of a `QuantAnalystNote`, or returning a different
    `status`/`reasoning`.
    """
    candidates = [_candidate("AAA"), _candidate("BBB")]
    keys = [candidate_key(c) for c in candidates]
    quant_by_key = {k: _qp() for k in keys}
    state = {"candidates": candidates, "quant_by_key": quant_by_key}

    out = await analyse_quant(state, _runtime(llm=None))

    assert set(out["quant_analyst_by_key"]) == set(keys)
    for k in keys:
        note = out["quant_analyst_by_key"][k]
        assert isinstance(note, QuantAnalystNote)
        assert note.status == "not_run"
        assert note.reasoning == "no LLM configured"
        assert note.replication_expectation is None


# ---------------------------------------------------------------------------
# TASK-4.3 -- more candidates than max_llm_candidates
# ---------------------------------------------------------------------------


async def test_candidates_beyond_the_cap_are_not_run_and_never_sent_to_the_llm(
    fake_analyst_client,
):
    """TASK-4.3/D-130: with more candidates than `runtime.context.
    max_llm_candidates`, the excess get `status="not_run"` with a reason
    naming the cap, and are never included in what is sent to the LLM at
    all -- the retained ones are the largest-|`origin_sigma`| (via TASK-3.12's
    `cap_for_llm`), not an arbitrary N and not alphabetical (`MarketScan`
    returns its leaders sorted by symbol, so an implementation that dropped
    `cap_for_llm` in favour of `list[:max_n]` would cap on the alphabet
    instead of on shock magnitude -- exactly the reachability gap D-130
    closed). No candidate is missing from the result either way (D-38).

    Falsifiable by: retaining an arbitrary/alphabetical subset instead of the
    largest-|sigma| four, sending a capped candidate's brief to the fake
    client, or dropping a capped candidate from the result entirely.
    """
    candidates = [
        _candidate("A", origin_sigma=-2.0),
        _candidate("B", origin_sigma=5.0),
        _candidate("C", origin_sigma=1.0),
        _candidate("D", origin_sigma=-4.0),
    ]
    keys = {c.symbol: candidate_key(c) for c in candidates}
    quant_by_key = {k: _qp() for k in keys.values()}
    responses = {
        keys[sym]: dict(
            status="ok",
            replication_expectation="high",
            flagged_concerns=[],
            reasoning=f"canned note for {sym}",
            model="fake-model",
        )
        for sym in ("B", "D")
    }
    client = fake_analyst_client(responses=responses)
    state = {"candidates": candidates, "quant_by_key": quant_by_key}

    out = await analyse_quant(state, _runtime(client, max_llm_candidates=2))

    result = out["quant_analyst_by_key"]
    assert set(result) == set(keys.values()), "no candidate may be missing (D-38)"

    # the two largest-|origin_sigma| candidates (B: 5.0, D: 4.0) got real notes
    assert result[keys["B"]].status == "ok"
    assert result[keys["D"]].status == "ok"

    # the two smallest-|origin_sigma| candidates (C: 1.0, A: 2.0) were capped
    for sym in ("A", "C"):
        note = result[keys[sym]]
        assert isinstance(note, QuantAnalystNote)
        assert note.status == "not_run"
        assert "batch cap reached" in note.reasoning
        assert "2" in note.reasoning, "the reason must name the cap (2)"

    # and never reached the fake client at all
    assert len(client.calls) == 1
    assert set(client.calls[0]["briefs"]) == {keys["B"], keys["D"]}


# ---------------------------------------------------------------------------
# TASK-4.4 -- the brief carries the numbers literally, and states the CI
# width caveat
# ---------------------------------------------------------------------------


async def test_brief_contains_ci_width_duplicate_count_and_split_half_pct_literally(
    fake_analyst_client,
):
    """TASK-4.4: the per-candidate brief handed to the LLM contains its CI
    width, duplicate count and split-half percentage as literal numbers --
    the model is handed the numbers PHASE-1 already computed, not raw
    `Evidence.detail` prose to re-derive them from.

    Falsifiable by: omitting any of the three numbers from the brief, or
    rendering them in a form that doesn't literally contain the value (e.g.
    only a qualitative bucket like "moderate").
    """
    candidate = _candidate("AAA")
    key = candidate_key(candidate)
    qp = _qp(median_ci_width=0.246, duplicate_count=7, split_half_sign_agree_pct=83.5)
    client = fake_analyst_client(
        default=dict(
            status="ok", replication_expectation="high", flagged_concerns=[],
            reasoning="canned", model="fake-model",
        )
    )
    state = {"candidates": [candidate], "quant_by_key": {key: qp}}

    await analyse_quant(state, _runtime(client))

    brief = client.calls[0]["briefs"][key]
    assert str(qp.median_ci_width) in brief
    assert str(qp.duplicate_count) in brief
    assert str(qp.split_half_sign_agree_pct) in brief


async def test_brief_reflects_candidate_is_etf_and_distinguishes_true_from_false(
    fake_analyst_client,
):
    """TASK-4.4: the ETF flag is present in the brief and actually reflects
    `candidate_is_etf` -- not a constant string that happens to always be
    present regardless of the flag's value. Two otherwise-identical
    perspectives, differing only in `candidate_is_etf`, must produce
    different briefs.

    Falsifiable by: an implementation that never mentions the ETF flag at
    all (both briefs would be identical), or one that mentions it but always
    renders the same text regardless of the flag's actual value.
    """
    etf_candidate = _candidate("ETFY")
    plain_candidate = _candidate("PLAIN")
    etf_key = candidate_key(etf_candidate)
    plain_key = candidate_key(plain_candidate)
    etf_qp = _qp(candidate_is_etf=True)
    plain_qp = _qp(candidate_is_etf=False)
    client = fake_analyst_client(
        default=dict(
            status="ok", replication_expectation="high", flagged_concerns=[],
            reasoning="canned", model="fake-model",
        )
    )
    state = {
        "candidates": [etf_candidate, plain_candidate],
        "quant_by_key": {etf_key: etf_qp, plain_key: plain_qp},
    }

    await analyse_quant(state, _runtime(client))

    briefs = client.calls[0]["briefs"]
    # the only field that differs between the two perspectives is
    # candidate_is_etf, so the two briefs must differ (once the symbol/as_of
    # header, which also differs, is discounted) precisely there.
    assert briefs[etf_key] != briefs[plain_key]
    assert str(True) in briefs[etf_key] or "ETF" in briefs[etf_key]
    assert str(False) in briefs[plain_key] or "ETF" not in briefs[plain_key] or (
        "candidate_is_etf=False" in briefs[plain_key]
    )


async def test_brief_states_median_ci_width_is_a_lower_bound(fake_analyst_client):
    """The team lead's addition to TASK-4.4: `median_ci_width` is
    systematically narrower than the truth -- PHASE-1 passed `trail` (an
    upper bound on valid sessions) to `confidence_interval` because
    `LagEdge` carries no per-edge valid-session count, while
    `comovement_edges` used the real pairwise valid-session count (D-100
    masks implausible returns) when it computed the correlation in the first
    place. Handing the model a number without that caveat invites it to
    reason from an over-confident interval -- the same principle as PHASE-5's
    TASK-5.5 ("the model is told what it cannot know"), applied here.

    Falsifiable by: a brief that states the CI width number but never says
    it is a lower bound / underestimate / narrower-than-true -- the model
    would then have no way to discount it appropriately.
    """
    candidate = _candidate("AAA")
    key = candidate_key(candidate)
    qp = _qp(median_ci_width=0.246)
    client = fake_analyst_client(
        default=dict(
            status="ok", replication_expectation="high", flagged_concerns=[],
            reasoning="canned", model="fake-model",
        )
    )
    state = {"candidates": [candidate], "quant_by_key": {key: qp}}

    await analyse_quant(state, _runtime(client))

    brief = client.calls[0]["briefs"][key].lower()
    assert "lower bound" in brief or "underestimate" in brief or "narrower than" in brief


async def test_brief_contains_split_half_min_abs_literally(fake_analyst_client):
    """Team lead's correction to TASK-1.2/TASK-4.4: the split-half magnitude
    carries 2.5-3.5x the incremental signal of the sign-agreement bit
    (measured), so if PHASE-1 computes it the brief must actually hand it to
    the model -- exactly the way `median_ci_width` and
    `split_half_sign_agree_pct` already are. A `QuantPerspective` field the
    brief never mentions is inert: the LLM can't reason over a number it was
    never shown.

    Falsifiable by: `_brief` omitting `qp.split_half_min_abs`, or rendering
    it in a form that doesn't literally contain the value (e.g. only a
    qualitative bucket like "well supported").
    """
    candidate = _candidate("AAA")
    key = candidate_key(candidate)
    qp = _qp(split_half_min_abs=0.318)
    client = fake_analyst_client(
        default=dict(
            status="ok", replication_expectation="high", flagged_concerns=[],
            reasoning="canned", model="fake-model",
        )
    )
    state = {"candidates": [candidate], "quant_by_key": {key: qp}}

    await analyse_quant(state, _runtime(client))

    brief = client.calls[0]["briefs"][key]
    assert str(qp.split_half_min_abs) in brief
