"""Core entities of the leader/lagger pipeline."""

from datetime import date, datetime

from pydantic import BaseModel


class Bar(BaseModel):
    """One daily price observation. Add OHLC fields when Q-05 settles the
    shock baseline (overnight-gap handling needs `open`)."""

    symbol: str
    close: float
    volume: int
    date: date


class Shock(BaseModel):
    """An abnormal price move detected in a leader equity."""

    symbol: str
    pct_change: float
    sigma: float
    lookback_days: int
    date: date


class LagEdge(BaseModel):
    """A leader -> lagger relationship read from the ArangoDB topology."""

    leader: str
    lagger: str
    correlation: float
    lag_days: int
    beta: float
    relation: str  # e.g. supplier, competitor, sector_peer, index_member


class NewsChunk(BaseModel):
    """A retrieved passage of unstructured news / earnings text."""

    doc_id: str
    symbol: str
    text: str
    published_at: datetime
    score: float


class Candidate(BaseModel):
    """A name to assess. The pipeline is agnostic to where it came from — an
    upstream signal today, a market scan later (see `adapters.candidates`)."""

    symbol: str
    direction: str  # "up" | "down" — the thesis being assessed
    as_of: date  # the daily anchor every node uses
    # the upstream alert instant, previously discarded at ingestion; needed for
    # intraday work but not yet read by any node (D-59/D-60)
    as_of_ts: datetime | None = None
    origin: str  # "external" | "scan" — provenance, kept so evaluation can slice on it
    # the shocked leader this candidate was discovered from (`MarketScan` only);
    # `None` for alert-fed (`origin="external"`) candidates
    origin_leader: str | None = None


class Evidence(BaseModel):
    """One corroborating or contradicting observation from the neighbourhood."""

    kind: str  # "leader_move" | "co_mention" | ...
    symbol: str  # the neighbour it came from
    supports: bool  # False = contradicts (D-19: the layer must be able to disagree)
    weight: float  # independence-discounted, not 1.0 per neighbour (Q-12)
    detail: str


class Assessment(BaseModel):
    """The pipeline's output: does the neighbourhood back this candidate?

    Deliberately not a buy/sell call — LagMatrix conditions an existing signal
    rather than originating one (D-18).
    """

    candidate: Candidate
    verdict: str  # "corroborated" | "contradicted" | "neutral"
    odds_adjustment: float  # log-odds delta applied to the upstream signal's prior
    effective_evidence: float  # independence-weighted, NOT a count of neighbours
    supporting: list[Evidence]
    contradicting: list[Evidence]
    rationale: str
    ts: datetime
    # a plain sentence naming the candidate's own thesis-signed move and the
    # leader that surfaced it; carries no vote (D-87 removed the earlier
    # classification fields as unfounded and non-predictive). None when the
    # candidate's own move is unknown — recorded in `errors` rather than
    # guessed (D-86).
    description: str | None = None
    # how many price-correlated neighbours fuse_evidence found for this
    # candidate (D-91) -- a fact about what the pipeline looked at, not a
    # claim about the market. Carries no weight and is never summed into
    # `effective_evidence`; it exists only so a reader can tell "no
    # neighbourhood to examine" (0) from "examined, and none of them moved"
    # (>0, with `supporting` and `contradicting` both empty) -- the two
    # were byte-identical before this field existed.
    neighbours: int
