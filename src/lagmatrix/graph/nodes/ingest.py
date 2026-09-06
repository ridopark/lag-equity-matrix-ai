"""Pull candidates from the configured CandidateSource into state.

Mode-agnostic: `external` today, `scan` later. See `adapters.candidates`.
"""

from lagmatrix.graph.state import LagMatrixState


def ingest(state: LagMatrixState) -> dict:
    """-> {"candidates": [...]}"""
    raise NotImplementedError
