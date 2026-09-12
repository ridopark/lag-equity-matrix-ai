"""RED for PHASE-8 (TASK-8.3), `PLAN-2026-09-11-quant-daytrade-perspectives.md`:
`serve.py`'s `stream()` must wire the direct Anthropic client into
`LagMatrixContext` the same way `runner.py` already wires the batch one
(`tests/test_runner_wiring.py`, PHASE-7's precedent), and the `/run` SSE
`start` event must say so.

Confirmed directly before writing this file: no `"llm"` string appears
anywhere in `scripts/serve.py` (`grep -n '"llm"\\|llm=' scripts/serve.py`
returns nothing), and neither `build_analyst_client` nor `load_settings` is
imported there. So today `ctx.llm` stays the `LagMatrixContext` dataclass
default (`None`) regardless of any API key, and the `emit("start", {...})`
payload carries no `llm` key at all -- both defects these tests pin.

**The seam.** `stream()` builds `LagMatrixContext` internally and returns
nothing -- only `emit(kind, payload)` callbacks are observable from outside.
Two spies, both already established precedent in this suite:

  - `serve.LagMatrixContext` is monkeypatched to a wrapper that records the
    kwargs `stream()` passes before delegating to the real class -- the same
    technique `tests/test_runner_wiring.py::_spy_context` uses on
    `runner_mod.LagMatrixContext`, applied here to `serve.py`'s own
    module-level import of the same class.
  - `emit` itself is a plain list-appending fake passed straight into
    `stream(source, limit, emit, as_of=...)` -- no HTTP layer, no
    `Handler`/`do_GET` involved, since TASK-8.1/8.2 are both inside
    `stream()`, not the request dispatch around it.

`source="synthetic"` throughout: reads only the committed
`tests/fixtures/synthetic-closes.parquet` / `synthetic-fires.csv`, needs no
`--allow-real`, and does not depend on `ALLOW_REAL`'s value. `arango_db` is
monkeypatched to return `None` unconditionally so no test reaches a socket,
regardless of whether an ArangoDB happens to be listening on this machine.
`as_of` is always passed explicitly (`AS_OF`, a date inside the synthetic
fixture's range) so `default_as_of()` -- which reads the ~650MiB real
`data/bars-10y.parquet` via `COMOVE_CLOSES()` -- is never invoked. `limit=1`
bounds every run to a single candidate so the full `graph.astream(...)`
still completes quickly. No network, no API key, no real LLM call: the one
test that needs `build_analyst_client` to return something other than `None`
mocks it with `fake_analyst_client` (`tests/conftest.py`), never a real
`ChatAnthropic`.

No implementation file is touched here.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

AS_OF = "2026-05-11"  # inside synthetic-closes.parquet's range; the module's
# own SYNTHETIC_FALLBACK_DATE, so no meaning is being invented here.


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


def _payload(events: list[tuple[str, dict]], kind: str) -> dict:
    matches = [p for k, p in events if k == kind]
    assert matches, f"no {kind!r} event emitted; kinds seen={[k for k, _ in events]}"
    return matches[0]


async def _run_stream(monkeypatch, source: str = "synthetic", limit: int | None = 1):
    """Runs the real `stream()` with `arango_db` stubbed to `None` (no
    network) and every emitted event collected in order."""
    import serve

    monkeypatch.setattr(serve, "arango_db", lambda: None)
    events: list[tuple[str, dict]] = []

    def _emit(kind, payload):
        events.append((kind, payload))

    await serve.stream(source, limit, _emit, as_of=AS_OF)
    return events


def _no_api_key(monkeypatch):
    monkeypatch.delenv("LAGMATRIX_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _spy_context(monkeypatch):
    """Records the kwargs `stream()` passes to `LagMatrixContext(...)`, then
    still constructs the real thing so the run proceeds normally -- the same
    technique as `tests/test_runner_wiring.py::_spy_context`, applied to
    `serve.py`'s own import of the class."""
    import serve

    captured: dict = {}
    real_ctx = serve.LagMatrixContext

    def _fake(**kwargs):
        captured.update(kwargs)
        return real_ctx(**kwargs)

    monkeypatch.setattr(serve, "LagMatrixContext", _fake)
    return captured


# ---------------------------------------------------------------------------
# TASK-8.2 -- the start event's "llm" flag, no API key configured
# ---------------------------------------------------------------------------


async def test_start_event_carries_llm_false_when_no_api_key_configured(monkeypatch):
    """With no API key configured anywhere, the `start` event must carry
    `"llm": False` -- present and `False`, not merely absent. A missing key
    and a `False` key are different signals to a page reading `d.llm`
    (`d.llm === undefined` vs. `d.llm === false`), the same distinction
    D-117's `"graphrag": db is not None` idiom already draws for ArangoDB.

    Falsifiable by: the `start` payload carrying no `"llm"` key at all
    (today's actual state -- confirmed directly, no `"llm"` string exists
    anywhere in `scripts/serve.py`), or carrying `None`/any value other than
    the literal `False`.
    """
    _no_api_key(monkeypatch)

    events = await _run_stream(monkeypatch)

    start = _payload(events, "start")
    assert "llm" in start, f"'llm' key missing from start event: {start}"
    assert start["llm"] is False, f"expected start event 'llm': False, got {start['llm']!r}"


async def test_no_api_key_configured_stream_still_completes_with_done_not_error(monkeypatch):
    """Guard, mirroring D-117's ArangoDB idiom applied to the LLM half: the
    page must not fail whole for want of a key. This already passes today
    (nothing currently raises for a missing key), which is expected -- it is
    here to catch a later change that makes the analyst-client lookup itself
    raise instead of degrading to `None`, not to prove TASK-8.1/8.2 exist.

    Falsifiable by: no `"done"` event ever being emitted, or an `"error"`
    event appearing anywhere in the stream.
    """
    _no_api_key(monkeypatch)

    events = await _run_stream(monkeypatch)

    kinds = [k for k, _ in events]
    assert "done" in kinds, f"expected a 'done' event, got kinds={kinds}"
    assert "error" not in kinds, f"unexpected 'error' event in stream: {events}"


# ---------------------------------------------------------------------------
# TASK-8.1 -- build_analyst_client(..., mode="direct") wired into ctx.llm
# ---------------------------------------------------------------------------


async def test_mocked_analyst_client_reaches_context_with_mode_direct_and_flips_start_event(
    monkeypatch, fake_analyst_client,
):
    """Proves three things at once, the way `tests/test_runner_wiring.py
    ::test_runner_forwards_batch_client_from_build_analyst_client_into_
    context` does for the batch path:

    1. `stream()` actually calls `build_analyst_client(..., mode="direct")`
       -- never `"batch"`. The plan's success criterion 3 requires this
       explicitly: Batch's up-to-24h latency would break the live SSE demo,
       and Direct's per-call cost would otherwise be paid on every nightly
       run instead of once. A later "optimisation" that swaps the mode
       silently must fail this test.
    2. `ctx.llm` is the *exact* object `build_analyst_client` returned, not
       merely non-`None`.
    3. The `start` event's `"llm"` flips to `True` once a client exists --
       proves the flag tracks the actual client rather than being hardcoded
       `False` (which `test_start_event_carries_llm_false_when_no_api_key_
       configured` alone cannot rule out).

    `fake_analyst_client(default={"status": "ok"})` matches
    `test_runner_wiring.py`'s construction exactly -- `status` is the one
    note field with no default, so a bare stub would raise `ValidationError`
    the moment the graph's analyst node actually calls `.classify()`.

    Falsifiable by: `calls` staying empty (`stream()` never calling
    `build_analyst_client` at all -- true today, since `serve.py` doesn't
    even import the name), `calls` containing `"batch"` instead of
    `"direct"`, `captured.get("llm")` not being the exact fake object, or
    the `start` event's `"llm"` staying `False`/absent despite a client
    existing.
    """
    _no_api_key(monkeypatch)  # the mock, not a real key, is what supplies the client
    import serve

    fake_client = fake_analyst_client(default={"status": "ok"})
    calls: list[str] = []

    def _fake_build_analyst_client(settings=None, *, mode):
        calls.append(mode)
        return fake_client

    monkeypatch.setattr(
        serve, "build_analyst_client", _fake_build_analyst_client, raising=False,
    )
    captured = _spy_context(monkeypatch)

    events = await _run_stream(monkeypatch)

    assert calls == ["direct"], (
        f"expected build_analyst_client(..., mode='direct') called exactly once, got {calls}"
    )
    assert captured.get("llm") is fake_client, (
        "ctx.llm must be exactly the object build_analyst_client returned, "
        f"got {captured.get('llm')!r}"
    )
    start = _payload(events, "start")
    assert start.get("llm") is True, f"expected start event 'llm': True, got {start.get('llm')!r}"


# ---------------------------------------------------------------------------
# TASK-8.1 -- Settings().max_llm_candidates reaching the context
# ---------------------------------------------------------------------------


async def test_settings_max_llm_candidates_env_override_reaches_context(monkeypatch):
    """`LagMatrixContext.max_llm_candidates` defaults to `None`, which
    *disables the cap entirely* (`cap_for_llm` is never applied) -- a
    fail-open default on the D-92 cost ceiling. This matters more on the
    live-demo path than the once-nightly batch one, since the demo can be
    re-run on every page load rather than once a day.

    `LAGMATRIX_MAX_LLM_CANDIDATES=7` is deliberately not `Settings`' own
    default of 20: a `stream()` that hardcodes 20 instead of reading
    `Settings` at call time would pass a weaker "is not None" check but
    fails this one.

    Falsifiable by: `captured["max_llm_candidates"]` staying `None` (never
    wired -- true today), or coming back `20` (the default, ignoring the env
    override) instead of `7`.
    """
    _no_api_key(monkeypatch)
    monkeypatch.setenv("LAGMATRIX_MAX_LLM_CANDIDATES", "7")
    captured = _spy_context(monkeypatch)

    await _run_stream(monkeypatch)

    assert captured.get("max_llm_candidates") == 7, (
        "expected Settings().max_llm_candidates (7 via env override) to reach "
        f"ctx.max_llm_candidates, got {captured.get('max_llm_candidates')!r}"
    )
