"""Core entities of the leader/lagger pipeline."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class ComovementEdge(BaseModel):
    """A pairwise contemporaneous co-movement relationship, measured directly
    from price history (D-95) — a calibrated confidence interval on how
    reliably two names move together, never a claim about what one does after
    the other."""

    a: str
    b: str
    corr: float
    n_sessions: int
    ci_low: float
    ci_high: float
    flag: str | None = None


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
    # the claiming leader's own signed z-score (`MarketScan` only); `None` for
    # alert-fed (`origin="external"`) candidates, which have no shock z
    origin_sigma: float | None = None


class Evidence(BaseModel):
    """One corroborating or contradicting observation from the neighbourhood."""

    kind: str  # "leader_move" | "co_mention" | ...
    symbol: str  # the neighbour it came from
    supports: bool  # False = contradicts (D-19: the layer must be able to disagree)
    weight: float  # independence-discounted, not 1.0 per neighbour (Q-12)
    detail: str


class QuantPerspective(BaseModel):
    """Deterministic read of a candidate's correlation neighbourhood (PHASE-1) --
    Fisher CI width, a duplicate-series flag and within-window split-half sign
    stability, all computed over `relation == "correlation"` edges only. Not
    read by `assess()`'s verdict/`effective_evidence`/`odds_adjustment` logic;
    exists for the (future) quant analyst node to reason over.
    """

    n_edges: int
    median_ci_width: float | None
    duplicate_count: int
    # % of correlation edges whose sign held between the first and second
    # halves of the same trailing window that produced `lag_edges` -- not
    # D-93's years-long discovery/validation split. None when there are no
    # correlation edges to split.
    split_half_sign_agree_pct: float | None
    # Median, over correlation edges, of min(|corr_first_half|,
    # |corr_second_half|) -- the *weaker* half's magnitude, not the
    # full-window correlation. `min` rather than mean: a pair strong in one
    # half and weak in the other is weakly supported, and the minimum says
    # so where a mean would let the strong half disguise it. None when
    # there are no correlation edges to split (matching
    # `split_half_sign_agree_pct`).
    split_half_min_abs: float | None = None
    candidate_is_etf: bool
    note: str
    # % of correlation edges whose leader shares the candidate's own 2-digit
    # SIC major-group prefix (not the full 4-digit code). None when
    # `arango_topology` is not configured, or when the candidate's own SIC
    # did not resolve -- there is no candidate-side sector to compare against.
    sector_match_pct: float | None = None
    # Among correlation edges, the count whose leader's co-mention PMI is
    # below/at-or-above `PMI_STRONG_THRESHOLD`. D-136: the two carry OPPOSITE
    # signs (an edge existing at all predicts failure; a higher PMI among
    # edged pairs predicts retention), so they are kept as two separate
    # counts, never averaged into one (D-135's mistake). A leader with no
    # co-mention edge at all counts toward neither. Both None when
    # `arango_topology` is not configured.
    comention_weak_count: int | None = None
    comention_strong_count: int | None = None


class DayTradePerspective(BaseModel):
    """Deterministic read of a candidate's own trailing liquidity and gap
    behaviour (PHASE-2), computed over sessions strictly before `as_of`
    (D-16) -- describes measured history only, never a forecast of
    tomorrow's session (D-96). Not read by `assess()`'s
    verdict/`effective_evidence`/`odds_adjustment` logic; exists for the
    (future) day-trade analyst node to reason over.
    """

    median_dollar_vol: float | None
    median_trade_count: float | None
    n_sessions: int
    gap_ratio: float | None
    # unconditional: this pipeline has no spread, slippage or borrow-rate
    # data anywhere (Q-33/Q-22), so absence is stated here rather than left
    # for a reader to infer from three fields that simply never appear.
    # Each entry: {"kind": ..., "reason": ...}.
    not_measurable: list[dict[str, str]]
    note: str


class QuantAnalystNote(BaseModel):
    """The quant analyst node's (PHASE-4) read of a `QuantPerspective` --
    classificatory only, never a calibrated probability (D-34: no `float`
    field on this model, structurally, so there is nowhere for one to hide).
    """

    status: Literal["ok", "not_run", "error"]
    replication_expectation: Literal["high", "low", "insufficient_data"] | None = None
    flagged_concerns: list[str] = Field(default_factory=list)
    reasoning: str = ""
    model: str = ""


class DayTradeAnalystNote(BaseModel):
    """The day-trade analyst node's (PHASE-5) read of a `DayTradePerspective`
    -- classificatory only, never a calibrated probability (D-34: no `float`
    field on this model, structurally).
    """

    status: Literal["ok", "not_run", "error"]
    liquidity_tier: Literal["ample", "marginal", "thin", "insufficient_data"] | None = None
    gap_dominant: bool | None = None
    reasoning: str = ""
    model: str = ""


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
    # quant_perspective/quant_analyst and day_trade_perspective/
    # day_trade_analyst (PHASE-6) -- not read by the verdict/
    # effective_evidence/odds_adjustment logic above; None only when the
    # candidate reached `assess()` without going through the graph's
    # `quant_perspective`/`day_trade_perspective` branches at all (e.g. a
    # test that calls `assess()` directly on hand-built state).
    quant: QuantPerspective | None = None
    quant_analyst: QuantAnalystNote | None = None
    day_trade: DayTradePerspective | None = None
    day_trade_analyst: DayTradeAnalystNote | None = None

