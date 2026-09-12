"""RED for PHASE-7 (PLAN-2026-09-11-quant-daytrade-perspectives), TASK-7.2 and
TASK-7.4 only: `runner.py`'s bars frame and Batch-client wiring into
`LagMatrixContext`.

TASK-7.5 (a real Anthropic Batch API run) is out of scope here -- no API key
is configured on this machine (`LAGMATRIX_ANTHROPIC_API_KEY`/
`ANTHROPIC_API_KEY` both unset, no `.env`), and it is a real, billed network
call in any case. Nothing below reaches the network, ArangoDB, Qdrant, or
postgres, and none of it reads the real `data/bars.parquet` -- every bars
fixture is a temp file under a `chdir`-ed `tmp_path`.

None of TASK-7.1/7.3 (reading `data/bars.parquet`; calling `load_settings()`/
`build_analyst_client()`) exist in `runner.py` yet -- confirmed directly:
`runner.py` imports neither `load_settings` nor `build_analyst_client`, and
never references `data/bars.parquet`. `LagMatrixContext.bars`/`.llm`/
`.max_llm_candidates` already exist (PHASE-6/PHASE-7 stubs in
`graph/context.py`) and default to `None`, which is exactly what makes some
of the tests below already pass today for the wrong reason -- called out
per test where it applies.

**The seam.** `run()` returns only `(assessments, thread_id, interrupt)` --
nothing exposes the `LagMatrixContext` it builds internally. Rather than
widen `run()`'s public return just for tests, these tests spy on
`runner_mod.LagMatrixContext` itself (the same already-imported module
attribute the tests below and `test_runner_signals.py`/
`test_runner_marketscan.py` already patch things like `ExternalSignals` on),
recording the kwargs `runner.py` actually passes before delegating to the
real class. This observes the one thing that matters -- what reaches the
context -- without depending on `runner.py`'s internal call shape otherwise.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from lagmatrix.domain.models import Candidate
from lagmatrix.pipeline import runner as runner_mod


def _candidate(sym: str, d: date = date(2026, 6, 1), direction: str = "up") -> Candidate:
    return Candidate(symbol=sym, direction=direction, as_of=d, origin="external")


class _StubSignals:
    """Hands back a fixed candidate list, tolerating any `as_of`."""

    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates

    def candidates(self, as_of: date | None = None) -> list[Candidate]:
        return self._candidates


def _patch_checkpoint_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner_mod, "CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite"))


def _spy_context(monkeypatch) -> dict:
    """Records the kwargs `runner.py` passes to `LagMatrixContext(...)`,
    then still constructs the real thing so the run proceeds normally.
    """
    captured: dict = {}
    real_ctx = runner_mod.LagMatrixContext

    def _fake(**kwargs):
        captured.update(kwargs)
        return real_ctx(**kwargs)

    monkeypatch.setattr(runner_mod, "LagMatrixContext", _fake)
    return captured


def _write_bars_parquet(path) -> None:
    """A two-row bars file for symbol CAND: one row ~360 days before the
    candidate's `as_of` (well outside the TRAIL_PAD window), one row inside
    it. Each row's `volume` is a distinctive marker so presence/absence in
    `ctx.bars` is unambiguous -- not merely "not None".
    """
    rows = pd.DataFrame({
        "symbol": ["CAND", "CAND"],
        "timestamp": [
            pd.Timestamp("2025-06-01", tz="UTC"),  # outside the TRAIL_PAD window
            pd.Timestamp("2026-03-02", tz="UTC"),  # inside it
        ],
        "open": [50.0, 50.0],
        "high": [50.0, 50.0],
        "low": [50.0, 50.0],
        "close": [50.0, 50.0],
        "volume": [918273, 273819],
        "trade_count": [100, 200],
        "vwap": [50.0, 50.0],
        "dollar_vol": [50.0 * 918273, 50.0 * 273819],
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(path)


async def _run_with_bars_fixture(tmp_path, monkeypatch, closes) -> dict:
    monkeypatch.chdir(tmp_path)
    _patch_checkpoint_db(monkeypatch, tmp_path)
    _write_bars_parquet(tmp_path / "data" / "bars.parquet")
    captured = _spy_context(monkeypatch)
    cand = _candidate("CAND")

    await runner_mod.run(
        cand.as_of, closes=closes, with_news=False, signals=_StubSignals([cand]),
    )
    return captured


# ---------------------------------------------------------------------------
# TASK-7.2 -- data/bars.parquet -> ctx.bars
# ---------------------------------------------------------------------------


async def test_bars_reaching_context_is_the_frame_read_from_the_file(closes, monkeypatch, tmp_path):
    """`ctx.bars` must carry the actual contents of `data/bars.parquet`, not
    merely something non-`None`. The in-window row's distinctive `volume`
    marker (273819) must be present in whatever DataFrame reaches
    `LagMatrixContext(bars=...)`.

    Falsifiable by: `runner.py` never reading `data/bars.parquet` at all
    (today's state -- `captured["bars"]` stays the dataclass default,
    `None`), or reading it but not forwarding the real frame (e.g.
    forwarding an empty/reconstructed one).
    """
    captured = await _run_with_bars_fixture(tmp_path, monkeypatch, closes)

    assert captured.get("bars") is not None, "bars= was never passed to LagMatrixContext"
    assert 273819 in set(captured["bars"]["volume"]), (
        "expected the in-window marker row from data/bars.parquet to reach "
        "ctx.bars unchanged"
    )


async def test_bars_older_than_the_trailing_window_are_trimmed_before_reaching_context(
    closes, monkeypatch, tmp_path,
):
    """The existing `TRAIL_PAD` convention (already applied to `closes` via
    `MarketFeed().daily_closes(syms, start, end)`) must also bound `bars`
    before it reaches the context -- a raw `pd.read_parquet(...)` with no
    windowing would otherwise load the file's entire history into every run.

    Falsifiable by: the far-past marker row (2025-06-01, ~360 days before
    the candidate's 2026-06-01 `as_of` -- well outside any `TRAIL_PAD`-sized
    window) surviving into `ctx.bars`.
    """
    captured = await _run_with_bars_fixture(tmp_path, monkeypatch, closes)

    bars_df = captured.get("bars")
    assert bars_df is not None, "bars= was never passed to LagMatrixContext"
    assert 918273 not in set(bars_df["volume"]), (
        "expected the far-past row to be trimmed by the trailing window "
        "before reaching ctx.bars"
    )


async def test_missing_bars_file_degrades_to_bars_none_without_raising(
    closes, monkeypatch, tmp_path
):
    """A fresh clone has no `data/bars.parquet` (same "fresh clone" situation
    `_load_excluded_symbols` already handles for `data/excluded-etfs.csv`);
    reading it must degrade to `bars=None`, not raise `FileNotFoundError`.

    Note: `captured.get("bars")` is `None` today too, but only because
    nothing wires `bars=` at all yet (`LagMatrixContext.bars` defaults to
    `None`) -- this test does not yet distinguish "correctly degraded" from
    "never wired"; `test_bars_reaching_context_is_the_frame_read_from_the_
    file` above is what proves the wiring exists, this one is what proves
    it stays correct once it does.

    Falsifiable by: `runner.run` raising `FileNotFoundError` (or any
    exception) once TASK-7.1 reads the file unconditionally.
    """
    monkeypatch.chdir(tmp_path)  # tmp_path has no data/bars.parquet at all
    _patch_checkpoint_db(monkeypatch, tmp_path)
    captured = _spy_context(monkeypatch)
    cand = _candidate("CAND")

    assessments, _thread_id, _interrupt = await runner_mod.run(
        cand.as_of, closes=closes, with_news=False, signals=_StubSignals([cand]),
    )

    assert captured.get("bars") is None
    assert assessments, "run should still produce an assessment with bars=None"


# ---------------------------------------------------------------------------
# TASK-7.4 -- Batch client / max_llm_candidates wiring, no API key configured
# ---------------------------------------------------------------------------


async def test_runner_forwards_batch_client_from_build_analyst_client_into_context(
    closes, monkeypatch, tmp_path, fake_analyst_client,
):
    """Proves `runner.py` actually calls `build_analyst_client(settings,
    mode="batch")` and forwards whatever it returns into `ctx.llm`,
    independent of whether a real API key is configured (that path is
    TASK-7.5, blocked). `build_analyst_client` is patched to hand back a
    `FakeAnalystClient` (not a bare sentinel) so the graph's analyst nodes
    can actually call `.classify()` on it without crashing.

    Falsifiable by: `runner.run` never calling `build_analyst_client` at all
    (`calls` stays empty -- true today, since `runner_mod` doesn't even
    import the name), calling it with `mode="direct"` instead of `"batch"`,
    or `ctx.llm` not being the exact object `build_analyst_client` returned.
    """
    _patch_checkpoint_db(monkeypatch, tmp_path)
    captured = _spy_context(monkeypatch)
    # `status` is deliberately the one note field with no default (7beb8ec:
    # a note must always say what it is, so a malformed construction cannot
    # read as a successful analysis). A fake with no `default` therefore
    # raises `ValidationError` the moment the graph really calls `classify`
    # -- which is exactly what this test wires up. Supply one.
    fake_client = fake_analyst_client(default={"status": "ok"})
    calls: list[str] = []

    def _fake_build_analyst_client(settings=None, *, mode):
        calls.append(mode)
        return fake_client

    monkeypatch.setattr(
        runner_mod, "build_analyst_client", _fake_build_analyst_client, raising=False,
    )

    cand = _candidate("CAND")
    await runner_mod.run(
        cand.as_of, closes=closes, with_news=False, signals=_StubSignals([cand]),
    )

    assert calls == ["batch"], (
        f"expected build_analyst_client(..., mode='batch') called exactly once, got {calls}"
    )
    assert captured.get("llm") is fake_client, (
        "ctx.llm must be exactly the object build_analyst_client returned"
    )


async def test_no_api_key_configured_degrades_every_candidate_to_a_not_run_analyst_note(
    closes, monkeypatch, tmp_path,
):
    """The "fresh clone, no credentials" precedent (`_load_excluded_symbols`,
    the ArangoDB/Qdrant suites): with no API key configured anywhere,
    `ctx.llm` must end up `None` and the run must still complete, producing
    an assessment for every candidate with a fully-formed `status="not_run"`
    analyst note on both perspectives -- not merely "does not crash".

    Note: this machine already has no key configured, so `ctx.llm is None`
    is not yet distinguishing "TASK-7.3 wired `build_analyst_client` and it
    correctly returned None" from "nothing calls `build_analyst_client` at
    all" (`LagMatrixContext.llm` defaults to `None` either way) --
    `test_runner_forwards_batch_client_from_build_analyst_client_into_
    context` above is what proves the call happens. What this test does pin,
    and what is not free today: every candidate actually reaching an
    assessment with `not_run` on *both* fields, for more than one candidate
    at once (isolation across the fan-out, not just a lucky single case).
    """
    monkeypatch.delenv("LAGMATRIX_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env in cwd to reintroduce a key
    _patch_checkpoint_db(monkeypatch, tmp_path)
    captured = _spy_context(monkeypatch)

    cands = [_candidate("CAND"), _candidate("CAND2")]
    assessments, _thread_id, _interrupt = await runner_mod.run(
        cands[0].as_of, closes=closes, with_news=False, signals=_StubSignals(cands),
    )

    assert captured.get("llm") is None
    assert len(assessments) == 2, f"expected an assessment per candidate, got {len(assessments)}"
    for a in assessments:
        assert a.quant_analyst is not None and a.quant_analyst.status == "not_run", a
        assert a.day_trade_analyst is not None and a.day_trade_analyst.status == "not_run", a


async def test_runner_passes_settings_max_llm_candidates_to_context(closes, monkeypatch, tmp_path):
    """`Settings.max_llm_candidates` -- the D-92-derived cost ceiling -- must
    actually reach `ctx.max_llm_candidates`. `LagMatrixContext.max_llm_
    candidates` defaults to `None`, which *disables the cap entirely*
    (`cap_for_llm` is never applied): a fail-open default on a cost control.
    Overriding the env to a value (7) other than `Settings`' own default
    (20) makes this bite -- a runner that hardcodes 20 instead of reading
    `Settings` at call time would pass a weaker "is not None" check but
    fails this one.

    Falsifiable by: `captured["max_llm_candidates"]` staying `None` (never
    wired -- true today), or coming back `20` (the default, ignoring the
    env override) instead of `7`.
    """
    monkeypatch.chdir(tmp_path)  # no .env to reintroduce a conflicting value
    monkeypatch.setenv("LAGMATRIX_MAX_LLM_CANDIDATES", "7")
    _patch_checkpoint_db(monkeypatch, tmp_path)
    captured = _spy_context(monkeypatch)

    cand = _candidate("CAND")
    await runner_mod.run(
        cand.as_of, closes=closes, with_news=False, signals=_StubSignals([cand]),
    )

    assert captured.get("max_llm_candidates") == 7, (
        f"expected Settings().max_llm_candidates (7 via env override) to reach "
        f"ctx.max_llm_candidates, got {captured.get('max_llm_candidates')!r}"
    )
