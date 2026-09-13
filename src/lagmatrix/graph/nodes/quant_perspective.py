"""Deterministic quant read of a candidate's correlation neighbourhood (PHASE-1).

Fisher CI width and the duplicate-series flag are never re-derived here --
both come from `lagmatrix.comovement`, the single place those calculations
live. This module adds one thing `comovement` doesn't: within-window
split-half sign stability, a cheap same-invocation check distinct from D-93's
years-long discovery/validation split.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from langgraph.runtime import Runtime

from lagmatrix.adapters.llm import cap_for_llm
from lagmatrix.comovement import confidence_interval, duplicate_flag
from lagmatrix.domain.models import Candidate, LagEdge, QuantAnalystNote, QuantPerspective
from lagmatrix.graph.context import LagMatrixContext
from lagmatrix.graph.state import LagMatrixState, candidate_key, edges_for

# median PMI among the 103 edged pairs in the 0.3-0.4 correlation band
# (scripts/measure_pmi_threshold.py) -- NOT the 0.4-0.5 band D-136 measured the
# 0.7451 AUC on, so the cutoff is not fitted on the sample that justified the
# feature. Below this is a "weak" co-mention edge, at or above it "strong";
# D-136 found the two carry OPPOSITE signs, so they are counted separately and
# never summed into one number.
PMI_STRONG_THRESHOLD = 1.1172


def compute_quant_perspective(
    candidate: Candidate,
    lag_edges: list[LagEdge],
    closes: pd.DataFrame,
    trail: int,
    excluded_etfs: frozenset[str],
    arango_topology: object | None = None,
) -> QuantPerspective:
    correlation_edges = [e for e in lag_edges if e.relation == "correlation"]
    n_edges = len(correlation_edges)

    ci_widths: list[float] = []
    dup_count = 0
    agree_count = 0
    split_half_min_abs: list[float] = []

    if correlation_edges:
        returns = closes.pct_change()
        sessions = closes.index
        # D-16: the window ends strictly before the as-of session. Production
        # sessions are indexed at 04:00 UTC, not midnight, so an exact
        # get_loc(midnight) lookup raises KeyError; searchsorted finds the
        # as-of session (or the first one after it) regardless of the
        # intraday offset the index uses.
        ti = sessions.searchsorted(pd.Timestamp(candidate.as_of, tz=sessions.tz))
        window = returns.iloc[ti - trail : ti]
        half = trail // 2
        first_half, second_half = window.iloc[:half], window.iloc[half:]

        for edge in correlation_edges:
            lo, hi = confidence_interval(edge.correlation, trail)
            ci_widths.append(hi - lo)

            if duplicate_flag(window[candidate.symbol], window[edge.leader]) is not None:
                dup_count += 1

            corr_first = first_half[candidate.symbol].corr(first_half[edge.leader])
            corr_second = second_half[candidate.symbol].corr(second_half[edge.leader])
            if np.sign(corr_first) == np.sign(corr_second):
                agree_count += 1
            split_half_min_abs.append(min(abs(corr_first), abs(corr_second)))

    sector_match_pct: float | None = None
    comention_weak_count: int | None = None
    comention_strong_count: int | None = None

    if arango_topology is not None:
        comention_weak_count = 0
        comention_strong_count = 0
        if correlation_edges:
            leaders = [e.leader for e in correlation_edges]
            sic = arango_topology.sic_of([candidate.symbol] + leaders)
            pmi = arango_topology.comention_pmi(candidate.symbol)

            candidate_sic = sic.get(candidate.symbol)
            if candidate_sic is not None:
                matches = sum(
                    1 for leader in leaders
                    if sic.get(leader) is not None and sic[leader][:2] == candidate_sic[:2]
                )
                sector_match_pct = matches / len(leaders) * 100.0

            for leader in leaders:
                if leader not in pmi:
                    continue
                if pmi[leader] >= PMI_STRONG_THRESHOLD:
                    comention_strong_count += 1
                else:
                    comention_weak_count += 1

    return QuantPerspective(
        n_edges=n_edges,
        median_ci_width=float(np.median(ci_widths)) if ci_widths else None,
        duplicate_count=dup_count,
        split_half_sign_agree_pct=(agree_count / n_edges * 100.0) if n_edges else None,
        split_half_min_abs=float(np.median(split_half_min_abs)) if split_half_min_abs else None,
        candidate_is_etf=candidate.symbol in excluded_etfs,
        note=(
            f"{n_edges} correlation edge(s) over a {trail}-session trailing window"
            if n_edges
            else "no correlation edges in this candidate's neighbourhood"
        ),
        sector_match_pct=sector_match_pct,
        comention_weak_count=comention_weak_count,
        comention_strong_count=comention_strong_count,
    )


def quant_perspective(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    """PHASE-6 `Send` target: the deterministic quant read for this branch's
    own candidate(s), feeding `quant_analyst`'s gather via `quant_by_key`."""
    closes = runtime.context.closes
    trail = runtime.context.trail
    excluded_etfs = runtime.context.excluded_symbols
    arango_topology = runtime.context.arango_topology

    candidates = [state["candidate"]] if "candidate" in state else state.get("candidates", [])

    quant_by_key = {
        candidate_key(c): compute_quant_perspective(
            c, edges_for(state, c), closes, trail, excluded_etfs, arango_topology=arango_topology
        )
        for c in candidates
    }

    out: dict = {"quant_by_key": quant_by_key}
    if "candidate" in state:
        out["candidate"] = state["candidate"]
    return out


QUANT_SYSTEM_PROMPT = """\
You are the quant analyst for a stock-lag pipeline. You are given a
deterministic read of one candidate's correlation neighbourhood: the number
of correlation edges, their median confidence-interval width, a duplicate-
series count, a within-window split-half sign-agreement percentage, the
weaker half's split-half correlation magnitude, and whether the candidate is
itself an ETF. Classify how much you'd expect this
candidate's correlation-based edges to replicate out of sample, and flag any
concerns (e.g. too few edges, high duplicate count, an ETF confounding the
read, or a narrow split-half agreement). Never report a calibrated
probability -- only a status and a classification.
"""


def _brief(candidate: Candidate, qp: QuantPerspective) -> str:
    return (
        f"{candidate.symbol} as of {candidate.as_of.isoformat()} ({candidate.direction})\n"
        f"n_edges={qp.n_edges}\n"
        f"median_ci_width={qp.median_ci_width} (a lower bound, not the true "
        f"interval: LagEdge carries no per-edge valid-session count, so this "
        f"Fisher CI was computed from `trail`, an upper bound on valid "
        f"sessions -- it is systematically narrower than / an underestimate "
        f"of the true width)\n"
        f"duplicate_count={qp.duplicate_count}\n"
        f"split_half_sign_agree_pct={qp.split_half_sign_agree_pct}\n"
        f"split_half_min_abs={qp.split_half_min_abs}\n"
        f"candidate_is_etf={qp.candidate_is_etf}\n"
        f"note: {qp.note}"
    )


async def analyse_quant(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
    quant_by_key = state.get("quant_by_key", {})
    candidates = [c for c in state.get("candidates", []) if candidate_key(c) in quant_by_key]
    llm = runtime.context.llm

    if llm is None:
        return {
            "quant_analyst_by_key": {
                candidate_key(c): QuantAnalystNote(
                    status="not_run", reasoning="no LLM configured"
                )
                for c in candidates
            }
        }

    max_n = runtime.context.max_llm_candidates
    if max_n is None:
        selected, capped = candidates, []
    else:
        selected, capped = cap_for_llm(candidates, max_n)

    result: dict[str, QuantAnalystNote] = {
        candidate_key(c): QuantAnalystNote(
            status="not_run", reasoning=f"batch cap reached ({max_n})"
        )
        for c in capped
    }

    briefs = {candidate_key(c): _brief(c, quant_by_key[candidate_key(c)]) for c in selected}
    if briefs:
        notes = await llm.classify(briefs, QuantAnalystNote, system_prompt=QUANT_SYSTEM_PROMPT)
        result.update(notes)

    return {"quant_analyst_by_key": result}
