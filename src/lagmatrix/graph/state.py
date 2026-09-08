"""The state object threaded through every node in the LagMatrix graph."""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from lagmatrix.domain.models import Assessment, Candidate, Evidence, LagEdge, NewsChunk, Shock


def candidate_key(c: Candidate) -> str:
    """Identity of one branch. A string, not a tuple, so it survives the
    checkpoint serializer unambiguously."""
    return f"{c.symbol}|{c.as_of.isoformat()}"


def edges_for(state: LagMatrixState, c: Candidate) -> list[LagEdge]:
    """This candidate's own neighbourhood edges.

    Prefers the keyed `lag_edges_by_key` channel, which disambiguates two
    candidates that share a symbol on different `as_of` dates within one
    batch. Falls back to filtering the flat `lag_edges` list by symbol when
    called directly (no graph, no keyed channel supplied) — unambiguous there
    since such a call only ever carries one candidate.
    """
    by_key = state.get("lag_edges_by_key")
    if by_key is not None:
        return by_key.get(candidate_key(c), [])
    return [e for e in state.get("lag_edges", []) if e.lagger == c.symbol]


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


def _last(_a, b):
    return b


class LagMatrixState(TypedDict, total=False):
    """One pass of the pipeline: candidates in, assessments out.

    Identical in both modes — only the CandidateSource behind `ingest` differs.
    Reducer-annotated keys accumulate across parallel branches; plain keys are
    last-write-wins.

    Candidates are fanned out per-branch via `Send` (PHASE-2). `lag_edges` and
    the flat `evidence`/`effective_evidence` keep their original shape for
    observability and direct node calls; the `_by_key` channels below them are
    keyed by `candidate_key` and are what `assess()` actually reads, so a batch
    cannot cross-attribute one candidate's neighbours, shocks, news or evidence
    to another.
    """

    # ingest — from the configured CandidateSource (external signal, or scan).
    candidates: list[Candidate]

    # the branch's own candidate, carried via the Send arg (V3/V4 — needs a
    # reducer because two branches must never collide on a plain channel).
    candidate: Annotated[Candidate, _last]

    # graph_retriever — point-in-time neighbourhood
    lag_edges: Annotated[list[LagEdge], add]
    lag_edges_by_key: Annotated[dict[str, list[LagEdge]], _merge]

    # leader_state — has the neighbourhood already moved?
    leader_shocks: Annotated[dict[str, list[Shock]], _merge]

    # vector_retriever — co-mention news
    news: Annotated[dict[str, list[NewsChunk]], _merge]

    # context_fusion — independence-weighted, not counted
    evidence: Annotated[list[Evidence], add]
    effective_evidence: float
    evidence_by_key: Annotated[dict[str, list[Evidence]], _merge]
    effective_evidence_by_key: dict[str, float]
    room_by_key: dict[str, float | None]
    origin_status_by_key: dict[str, str | None]

    # assessor
    assessments: list[Assessment]

    # review — human confirmation of a contradicted verdict (PHASE-6)
    approved: bool

    # cross-cutting
    errors: Annotated[list[str], add]
