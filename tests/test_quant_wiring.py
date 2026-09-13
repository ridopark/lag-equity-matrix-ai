"""RED for PHASE-6 (PLAN-2026-09-11-quant-daytrade-perspectives): wiring
`quant_perspective` -> `quant_analyst` -> `assessor` into the real graph.

PHASE-1 (`compute_quant_perspective`) and PHASE-4 (`analyse_quant`) already
exist and are unit-tested in isolation (`test_quant_perspective.py`,
`test_quant_analyst.py`). Nothing yet calls either of them from
`build_graph()` -- `Assessment` has no `quant`/`quant_analyst` field, and
neither node name appears in the compiled graph (confirmed directly against
`build_graph(with_news=False).get_graph().nodes` and
`Assessment.model_fields` before writing this file: both come back empty).
These tests pin the wiring itself, not the math PHASE-1/4 already cover.
"""

from __future__ import annotations

from datetime import date

from lagmatrix.domain.models import Candidate
from lagmatrix.graph.nodes.quant_perspective import PMI_STRONG_THRESHOLD
from lagmatrix.graph.state import candidate_key


def _candidate(symbol: str, d: date = date(2026, 6, 1), direction: str = "up") -> Candidate:
    return Candidate(symbol=symbol, direction=direction, as_of=d, origin="external")


def test_no_llm_configured_populates_quant_but_leaves_analyst_not_run(run_graph):
    """TASK-6.2: `llm=None` through the real graph -- the deterministic
    PHASE-1 read still runs (`assessments[0].quant` populated) while the
    PHASE-4 gather node reports it never called an LLM
    (`assessments[0].quant_analyst.status == "not_run"`), reached through
    the graph rather than called directly as PHASE-4's own tests do.

    Falsifiable by: `quant` staying `None`/the attribute being absent (the
    node was never wired or never reached), or `quant_analyst` being absent,
    or `quant_analyst.status` being anything other than `"not_run"`.
    """
    out = run_graph([_candidate("CAND")])
    a = out["assessments"][0]

    assert a.quant is not None
    assert a.quant_analyst is not None
    assert a.quant_analyst.status == "not_run"


def test_injected_llm_produces_an_ok_note_matching_the_canned_response(
    run_graph, fake_analyst_client,
):
    """TASK-6.3: a fake `AnalystClient` injected via `run_graph(llm=...)` --
    `assessments[0].quant_analyst.status == "ok"` and its `reasoning`
    matches the fake client's canned note, proving the injected client is
    actually threaded through `quant_perspective -> quant_analyst ->
    assessor`, not merely accepted and ignored.

    Falsifiable by: `status` staying `"not_run"`/`"error"` despite an `llm`
    being configured, or `reasoning` not matching the canned value (e.g.
    `assess()` hard-codes a note instead of reading
    `quant_analyst_by_key`).
    """
    candidate = _candidate("CAND")
    key = candidate_key(candidate)
    client = fake_analyst_client(
        default=dict(
            status="ok",
            replication_expectation="high",
            flagged_concerns=[],
            reasoning=f"canned quant note for {key}",
            model="fake-model",
        )
    )

    out = run_graph([candidate], llm=client)
    a = out["assessments"][0]

    assert a.quant_analyst.status == "ok"
    assert a.quant_analyst.reasoning == f"canned quant note for {key}"


def test_two_candidates_do_not_cross_attribute_quant_fields(run_graph, fake_analyst_client):
    """TASK-6.4 -- the halt condition. CAND and CAND2 (independent
    correlation blocs in `conftest.closes` -- LEAD1/LEAD2 vs LEAD4/LEAD5,
    drawn from separate rng calls) fanned out together: each assessment's
    `quant` and `quant_analyst` must reflect only its own candidate, not the
    other's and not a merge of both (D-89's exact failure class).

    Genuinely discriminating, not coincidentally passing: the fake client
    returns a different canned `reasoning` string per `candidate_key`, and
    the guard assertion below confirms the two candidates' real
    `quant.median_ci_width` values (computed independently of any canned
    response) actually differ before trusting the rest -- a test that
    reached this point with identical values on both sides would let a swap
    or a merge through unnoticed.

    Cross-attribution is checked two ways: against the other candidate's
    canned note directly, and against what each candidate produces when run
    alone (mirrors the existing `test_two_candidates_do_not_cross_attribute`
    idiom in `test_fanout.py` for the pre-existing evidence fields).

    Falsifiable by: either assessment's `quant_analyst.reasoning` containing
    the OTHER candidate's canned string (a swap), the batched run's `quant`/
    `quant_analyst` for either candidate differing from that same
    candidate's solo run (a merge/overwrite across the two `Send` branches),
    or the two candidates' real `quant.median_ci_width` values coming out
    equal (which would make a swap undetectable and the rest of this test
    worthless).
    """
    cand, cand2 = _candidate("CAND"), _candidate("CAND2")
    key1, key2 = candidate_key(cand), candidate_key(cand2)
    response1 = dict(
        status="ok", replication_expectation="high", flagged_concerns=[],
        reasoning=f"quant note for {key1}", model="fake-model",
    )
    response2 = dict(
        status="ok", replication_expectation="low", flagged_concerns=["thin data"],
        reasoning=f"quant note for {key2}", model="fake-model",
    )
    batched_client = fake_analyst_client(responses={key1: response1, key2: response2})

    batched = run_graph([cand, cand2], llm=batched_client)
    solo1 = run_graph([cand], llm=fake_analyst_client(responses={key1: response1}))
    solo2 = run_graph([cand2], llm=fake_analyst_client(responses={key2: response2}))

    by_key = {candidate_key(a.candidate): a for a in batched["assessments"]}
    a1, a2 = by_key[key1], by_key[key2]
    solo_a1, solo_a2 = solo1["assessments"][0], solo2["assessments"][0]

    # guard: the real, deterministic quant reads must actually differ --
    # otherwise a swap between the two would be invisible to the assertions
    # below.
    assert a1.quant.median_ci_width != a2.quant.median_ci_width

    assert a1.quant_analyst.reasoning == f"quant note for {key1}"
    assert a2.quant_analyst.reasoning == f"quant note for {key2}"
    assert a1.quant == solo_a1.quant
    assert a2.quant == solo_a2.quant
    assert a1.quant_analyst == solo_a1.quant_analyst
    assert a2.quant_analyst == solo_a2.quant_analyst


class _FakeTopology:
    """RED for PHASE-5 (PLAN-2026-09-12-relatedness-and-sectors): fake
    `ArangoTopology`, mirroring `test_nodes.py`'s `FakeArangoTopology`
    pattern. `laggers_of` returns nothing so this double stays focused on
    the relatedness fields under test here rather than also perturbing
    `retrieve_neighbourhood`'s edge set -- `laggers_of` is still called
    unconditionally whenever `arango_topology` is not `None` (D-79), so it
    must exist even though this file has nothing to assert about it.
    """

    def __init__(self, sic: dict[str, str] | None = None, pmi: dict[str, float] | None = None):
        self._sic = sic or {}
        self._pmi = pmi or {}

    def sic_of(self, symbols: list[str]) -> dict[str, str]:
        return {s: self._sic[s] for s in symbols if s in self._sic}

    def comention_pmi(self, symbol: str) -> dict[str, float]:
        return dict(self._pmi)

    def laggers_of(self, leader, max_hops, as_of):
        return []


def test_no_arango_topology_leaves_relatedness_fields_none(run_graph):
    """TASK-5.8: `run_graph` defaults to `arango_topology=None` -- the same
    "`None` disables the feature" contract PHASE-5's unit tests already pin
    at `compute_quant_perspective`'s level, checked here end to end through
    the compiled graph rather than the bare function.

    Falsifiable by: any of the three relatedness fields on
    `assessments[0].quant` coming back as `0`/`0.0` instead of `None`.
    """
    out = run_graph([_candidate("CAND")])
    quant = out["assessments"][0].quant

    assert quant.sector_match_pct is None
    assert quant.comention_weak_count is None
    assert quant.comention_strong_count is None


def test_injected_topology_surfaces_actual_relatedness_values(run_graph):
    """TASK-5.8 -- the Q-61-shaped wiring guard: a forgotten
    `arango_topology=` at the `quant_perspective()` call site would leave
    the three fields silently `None` while every other test in this file
    still passes, so this asserts the *specific* expected values reached
    through the real graph, not merely "not None".

    `signal_universe` drops every symbol except LEAD1/LEAD2 from CAND's
    correlation pool (`conftest.closes` correlates CAND tightly with only
    that bloc), so CAND's correlation edges are known exactly -- the same
    two-leader setup `test_sector_match_pct_counts_same_2digit_sic_prefix`
    uses at the unit level, reached this time end to end.

    Falsifiable by: the injected topology's `sic`/`pmi` values never
    reaching `Assessment.quant` (proves TASK-5.7 was skipped, or the
    context wiring drops `arango_topology` before it reaches
    `compute_quant_perspective`).
    """
    fake = _FakeTopology(
        sic={"CAND": "7372", "LEAD1": "7371", "LEAD2": "3674"},
        pmi={
            "LEAD1": PMI_STRONG_THRESHOLD + 0.5,
            "LEAD2": PMI_STRONG_THRESHOLD - 0.5,
        },
    )

    out = run_graph(
        [_candidate("CAND")],
        signal_universe={"LEAD3", "INDEP", "LEAD4", "LEAD5", "CAND2", "CANDD"},
        arango_topology=fake,
    )
    quant = out["assessments"][0].quant

    assert quant.sector_match_pct == 50.0
    assert quant.comention_strong_count == 1
    assert quant.comention_weak_count == 1
