"""D-59/D-60: the upstream feed's `posted_at` carries a full timestamp
(`2026-05-29 14:34:10.936+00`), but `ExternalSignals.candidates()` truncates
it to a `date` at ingestion (`adapters/candidates.py:38`) and `Candidate` has
nowhere to put the rest — so it is lost before any intraday work could use
it.

These tests pin the fix as purely additive: `Candidate` gains an optional
`as_of_ts: datetime | None` carrying the full instant, while `as_of` keeps
its existing meaning and format exactly. In particular `candidate_key`
(`graph/state.py`) must stay blind to the new field — it is what lets two
same-day alerts on one symbol collapse into a single branch today, and the
synthetic baseline (e.g. AMZN 2026-07-24 appearing twice) depends on that.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from lagmatrix.domain.models import Candidate


def _write_fires_csv(tmp_path, rows: list[tuple[str, str, str]]):
    path = tmp_path / "fires.csv"
    with path.open("w") as fh:
        fh.write("ticker,posted_at,direction\n")
        for ticker, posted_at, direction in rows:
            fh.write(f"{ticker},{posted_at},{direction}\n")
    return path


def test_external_signals_populates_as_of_ts_with_full_instant(tmp_path):
    """`ExternalSignals.candidates()` must parse the whole `posted_at` value
    into `as_of_ts` — hour/minute/second and timezone included — not just
    the date. `as_of` must still be the plain date.

    Falsifies if: `as_of_ts` is missing/None for a row with a real time
    component, or its hour/minute/second/tzinfo don't match the source row.
    """
    from lagmatrix.adapters.candidates import ExternalSignals

    path = _write_fires_csv(
        tmp_path, [("SYNA", "2026-05-29 14:34:10.936+00", "up")]
    )
    [cand] = ExternalSignals(path).candidates()

    assert cand.as_of == date(2026, 5, 29)
    assert cand.as_of_ts == datetime(2026, 5, 29, 14, 34, 10, 936000, tzinfo=UTC)


def test_candidate_key_ignores_as_of_ts():
    """Two Candidates identical except for `as_of_ts` must produce the same
    `candidate_key`, and that key's format must stay exactly `symbol|as_of`
    — no time component leaking in. This is what keeps two same-day alerts
    on one symbol collapsing into a single branch (the synthetic baseline
    depends on it, e.g. AMZN 2026-07-24 appearing twice).

    Falsifies if: the two candidates' keys differ, or the key contains
    anything from `as_of_ts` (e.g. a time-of-day suffix).
    """
    from lagmatrix.graph.state import candidate_key

    base = dict(symbol="AMZN", direction="up", as_of=date(2026, 7, 24), origin="external")
    early = Candidate(**base, as_of_ts=datetime(2026, 7, 24, 9, 30, tzinfo=UTC))
    late = Candidate(**base, as_of_ts=datetime(2026, 7, 24, 15, 45, tzinfo=UTC))

    assert candidate_key(early) == candidate_key(late) == "AMZN|2026-07-24"


def test_external_signals_tolerates_missing_or_malformed_posted_at_time(tmp_path):
    """A row whose `posted_at` has no time component, or is malformed, must
    still yield a usable Candidate — `as_of` derived from the leading 10
    chars as today, `as_of_ts` falling back to `None` rather than raising.

    Falsifies if: `ExternalSignals.candidates()` raises for either row, or
    the resulting `Candidate.as_of` is wrong.
    """
    from lagmatrix.adapters.candidates import ExternalSignals

    path = _write_fires_csv(
        tmp_path,
        [
            ("SYNB", "2026-04-27", "up"),  # no time component at all
            ("SYNC", "2026-04-27 not-a-time", "down"),  # malformed tail
        ],
    )
    out = ExternalSignals(path).candidates()

    assert [c.symbol for c in out] == ["SYNB", "SYNC"]
    assert all(c.as_of == date(2026, 4, 27) for c in out)
    assert all(c.as_of_ts is None for c in out)


def test_candidate_round_trips_as_of_ts_through_checkpoint_serializer():
    """A `Candidate` with `as_of_ts` set must survive the checkpoint
    serializer's allowlist round-trip as a `Candidate` (not a dict), with
    `as_of_ts` intact — same guard `test_checkpointing.py` applies to the
    domain models generally (D-44), now covering the new datetime field.

    Falsifies if: serializing then deserializing loses `as_of_ts`, changes
    its type, or the round-tripped value is no longer a `Candidate`.
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    _ALLOWED_MODELS = [("lagmatrix.domain.models", "Candidate")]
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODELS)

    cand = Candidate(
        symbol="AMZN",
        direction="up",
        as_of=date(2026, 7, 24),
        as_of_ts=datetime(2026, 7, 24, 15, 45, 12, 500000, tzinfo=UTC),
        origin="external",
    )

    type_, payload = serde.dumps_typed(cand)
    restored = serde.loads_typed((type_, payload))

    assert isinstance(restored, Candidate)
    assert restored.as_of_ts == cand.as_of_ts
