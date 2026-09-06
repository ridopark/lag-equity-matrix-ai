"""`run()`'s signal source must be injectable, not just constructed twice.

`runner.run` currently constructs `ExternalSignals()` at two call sites: once
for the `as_of`-filtered candidate list, once again (unfiltered) for
`signal_universe`. Both hard-code the default path (`data/fires.csv`), which
is not committed — a fresh clone cannot run the pipeline at all. These tests
pin an injected keyword-only `signals=` parameter that both call sites must
use, defaulting to `ExternalSignals()` so existing callers are unaffected.
"""

from __future__ import annotations

from datetime import date

from lagmatrix.domain.models import Candidate
from lagmatrix.pipeline import runner as runner_mod


class _StubSignals:
    """Records every `as_of` it was asked for, in call order."""

    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates
        self.calls: list[date | None] = []

    def candidates(self, as_of: date | None = None) -> list[Candidate]:
        self.calls.append(as_of)
        return self._candidates


def _boom(*_args, **_kwargs):
    raise AssertionError("default signal source constructed")


async def test_run_uses_injected_signals_for_both_candidate_lookups(closes, monkeypatch, tmp_path):
    """Both the as_of-filtered candidate list and the unfiltered
    `signal_universe` must come from the injected `signals=` object, not from
    a default `ExternalSignals()` constructed internally.

    Falsifies if: `run()` has no `signals=` kwarg (TypeError), or an
    implementation that injects `signals` at only one of the two call sites
    (e.g. the `as_of`-filtered lookup) still lets the other call site
    construct the default `ExternalSignals()` — the patched constructor
    raises immediately if invoked at all, and the stub's recorded call
    pattern must show exactly one as_of-filtered call and one unfiltered
    call.
    """
    monkeypatch.setattr(runner_mod, "ExternalSignals", _boom)
    monkeypatch.setattr(runner_mod, "CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite"))

    as_of = date(2026, 3, 26)
    cand = Candidate(symbol="CAND", direction="up", as_of=as_of, origin="external")
    stub = _StubSignals([cand])

    assessments, _thread_id, _interrupt = await runner_mod.run(
        as_of,
        closes=closes,
        with_news=False,
        limit=5,
        signals=stub,
    )

    assert stub.calls == [as_of, None], (
        "expected one as_of-filtered call (candidate list) and one "
        f"unfiltered call (signal_universe) against the injected source, got {stub.calls}"
    )
    assert assessments, "run should have produced an assessment for the injected candidate"


async def test_run_uses_default_external_signals_when_not_injected(monkeypatch):
    """With no `signals=` kwarg, `run()` must still fall back to a default
    `ExternalSignals()` — the injection must be optional, not required.

    Falsifies if: the patched `ExternalSignals` constructor is never invoked
    (i.e. the default path silently stopped being used, or `run()` no
    longer accepts being called without `signals=`).
    """
    constructed: list[tuple] = []

    def factory(*args, **kwargs):
        constructed.append((args, kwargs))
        return _StubSignals([])

    monkeypatch.setattr(runner_mod, "ExternalSignals", factory)

    assessments, _thread_id, _interrupt = await runner_mod.run(date(2026, 3, 26))

    assert constructed, (
        "expected ExternalSignals() to be constructed by default when signals= is omitted"
    )
    assert assessments == []
