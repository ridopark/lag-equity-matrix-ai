"""PHASE-3: a checkpointer makes a mid-run failure resumable per `thread_id`.

Today `build_graph()` compiles with no checkpointer, so a run that dies
mid-fan-out has nothing to resume from — the whole batch must restart. These
tests pin the checkpointed shape (V10): a killed run's successful branches
survive in `get_state`, and re-invoking on the same `thread_id` re-executes
only the branch that failed.

They also pin that a `Candidate`/`Assessment` pydantic model survives the
checkpoint serializer as itself (not a dict), and that doing so does not log
the serializer's "unregistered type" deprecation once the domain models are
explicitly allowlisted (D-44) — `saver.with_allowlist(...)` alone does
**not** achieve this (it is a no-op outside `LANGGRAPH_STRICT_MSGPACK`); the
serializer must be constructed with `allowed_msgpack_modules=` directly.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde import jsonplus
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from lagmatrix.domain.models import (
    Assessment,
    Bar,
    Candidate,
    Evidence,
    LagEdge,
    NewsChunk,
    Shock,
)
from lagmatrix.graph.builder import build_graph
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.nodes.leader_state import leader_state as real_leader_state
from lagmatrix.graph.state import candidate_key

# D-44: the msgpack allowlist is per (module, class name), not per module —
# every domain model that can end up in checkpointed state must be listed.
_ALLOWED_MODELS = [
    ("lagmatrix.domain.models", cls.__name__)
    for cls in (Candidate, Assessment, Evidence, LagEdge, Shock, NewsChunk, Bar)
]

# topk=3 so each candidate's neighbourhood is its own bloc — see test_fanout.py.
TOPK = 3


def _candidate(sym, d=date(2026, 6, 1), direction="up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


class LeaderStateBoom(Exception):
    """Bespoke, not one of `default_retry_on`'s exempted stdlib types (V8) —
    irrelevant here since PHASE-3 adds no retry policy, but keeping it
    bespoke also means it can't be confused with a real pipeline error."""


def test_a_failed_run_resumes_only_the_failed_branch(closes, monkeypatch):
    """A run that dies in `leader_state` for one of three candidates leaves
    the other two branches' writes in `get_state`; resuming re-runs only the
    failed branch, and the final state holds all three.

    Falsifies if: the first `invoke` does not raise, `get_state` after the
    failure is missing a successful branch's `leader_shocks`, the resumed
    `invoke` calls `leader_state`'s body more than once, or the final state
    is missing any of the three candidates' assessments.
    """
    candidates = [_candidate("CAND"), _candidate("CAND2"), _candidate("CANDD", direction="down")]
    fail_symbol = "CAND2"  # the second of the three
    flag = {"fail": True}
    calls: list[str] = []

    def patched_leader_state(state, runtime):
        c = state.get("candidate")
        calls.append(c.symbol if c is not None else "?")
        if flag["fail"] and c is not None and c.symbol == fail_symbol:
            raise LeaderStateBoom(f"boom on {c.symbol}")
        return real_leader_state(state, runtime)

    monkeypatch.setattr("lagmatrix.graph.builder.leader_state", patched_leader_state)

    graph = build_graph(with_news=False, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK)
    config = {"configurable": {"thread_id": "resume-test"}}

    with pytest.raises(LeaderStateBoom):
        graph.invoke({"candidates": candidates}, context=ctx, config=config)

    values = graph.get_state(config).values
    shocks_by_key = values.get("leader_shocks", {})
    assert candidate_key(candidates[0]) in shocks_by_key
    assert candidate_key(candidates[2]) in shocks_by_key
    assert candidate_key(candidates[1]) not in shocks_by_key
    assert len(calls) == 3, "all three branches should have been attempted once each"

    flag["fail"] = False
    calls.clear()
    out = graph.invoke(None, context=ctx, config=config)

    assert calls == ["CAND2"], "resume must re-run only the previously-failed branch"
    assert {a.candidate.symbol for a in out["assessments"]} == {"CAND", "CAND2", "CANDD"}


def test_thread_id_isolates_runs(closes):
    """Two invokes under different `thread_id`s, over different candidate
    lists, must not see each other's checkpointed state.

    Falsifies if: `get_state` for one thread contains the other thread's
    candidate in `lag_edges_by_key` — i.e. runs are not actually isolated by
    `thread_id`.
    """
    graph = build_graph(with_news=False, checkpointer=InMemorySaver())
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK)

    cand_a, cand_b = _candidate("CAND"), _candidate("CAND2")
    config_a = {"configurable": {"thread_id": "thread-a"}}
    config_b = {"configurable": {"thread_id": "thread-b"}}

    graph.invoke({"candidates": [cand_a]}, context=ctx, config=config_a)
    graph.invoke({"candidates": [cand_b]}, context=ctx, config=config_b)

    edges_a = graph.get_state(config_a).values["lag_edges_by_key"]
    edges_b = graph.get_state(config_b).values["lag_edges_by_key"]

    assert candidate_key(cand_a) in edges_a
    assert candidate_key(cand_b) not in edges_a
    assert candidate_key(cand_b) in edges_b
    assert candidate_key(cand_a) not in edges_b


def test_checkpointed_state_round_trips_pydantic_models(closes, monkeypatch, caplog):
    """An `Assessment` survives the checkpoint serializer as an `Assessment`
    instance, and allowlisting the domain models (D-44) suppresses the
    serializer's "unregistered type" deprecation log.

    `_warned_unregistered_types` (the serializer's dedup cache) is reset
    first — it is process-global and warns only once per type per process,
    so without the reset this assertion would pass for the wrong reason
    whenever an earlier test happened to trigger it first.

    Falsifies if: `assessments[0]` deserializes as a dict instead of an
    `Assessment`, `.candidate.symbol` does not read back as "CAND", or a
    "Deserializing unregistered type" record is logged despite the
    allowlisted serializer.
    """
    monkeypatch.setattr(jsonplus, "_warned_unregistered_types", set())
    caplog.set_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus")

    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODELS)
    graph = build_graph(with_news=False, checkpointer=InMemorySaver(serde=serde))
    ctx = LagMatrixContext(closes=closes, signal_universe=set(), topk=TOPK)
    config = {"configurable": {"thread_id": "roundtrip-test"}}

    graph.invoke({"candidates": [_candidate("CAND")]}, context=ctx, config=config)

    assessment = graph.get_state(config).values["assessments"][0]
    assert isinstance(assessment, Assessment)
    assert assessment.candidate.symbol == "CAND"

    unregistered = [r.message for r in caplog.records if "unregistered type" in r.message]
    assert not unregistered, f"unexpected deprecation warning(s): {unregistered}"
