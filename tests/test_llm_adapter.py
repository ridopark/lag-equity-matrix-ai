"""RED for PHASE-3 (PLAN-2026-09-11-quant-daytrade-perspectives):
`Settings`/`load_settings`, the `AnalystClient` split, and `cap_for_llm`.

Nothing in this file makes a real network call, ever -- not even a skipped
one. Every `ChatAnthropic`/`anthropic.Anthropic` stand-in is a fake defined
below; no test needs `ANTHROPIC_API_KEY`.

`DirectAnalystClient`, `BatchAnalystClient`, `build_analyst_client` and
`cap_for_llm` do not exist yet -- TASK-3.5/3.8/3.10/3.11/3.12 belong to the
green phase. These tests pin four things the green implementation must
satisfy:

* `load_settings()` reads `Settings` from the environment, and the model
  default is the Haiku id -- an owner decision that could silently regress
  (TASK-3.4);
* `DirectAnalystClient.classify` calls `.with_structured_output(schema)
  .ainvoke(...)` once per key, concurrently, and assembles messages so the
  fixed `system_prompt` sits alone in a cached block and the per-candidate
  brief sits alone in an uncached one -- the D-16 property, asserted
  structurally (TASK-3.7);
* `BatchAnalystClient.classify` builds one batch request per key, polls
  `.retrieve()` until `processing_status == "ended"`, parses each result's
  forced-tool-use block into `schema` keyed by `custom_id`, and assembles
  the exact same cached/uncached split as the direct path -- proven by
  comparing what actually reaches each fake, not by naming a private
  function (TASK-3.9, see the note on that choice below);
* `AnalystClient.classify`'s D-38 contract -- a key that cannot be
  classified comes back `schema(status="error", ...)`, never missing from
  the result dict -- holds on both clients;
* `cap_for_llm` orders by `abs(sigma)` descending when every candidate
  carries a known sigma, and falls back to list order otherwise, splitting
  `(selected, capped)` at `max_n` (TASK-3.13).

A design choice worth stating: TASK-3.9 asks to prove Direct and Batch use
"the same shared prompt-assembly function." Nothing in the plan names that
function, and asserting against a private symbol we invent here would pin
an implementation detail rather than a behaviour (see `test_quant_
perspective.py`'s `confidence_interval`/`duplicate_flag` spies for
contrast -- those already existed as a public API before that plan; this
one does not). Instead, `test_direct_and_batch_assemble_identical_cache_
boundary_for_the_same_inputs` below runs both clients against the same
`system_prompt`/`briefs` and asserts the structures each actually sends to
its fake are byte-identical. That is what "one function, not two" is
observable as, and it fails exactly when the two paths drift -- which is
the failure this test exists to catch -- regardless of how green
factors the code.

A second gap surfaced while writing TASK-3.13, and is now **closed** --
kept here because the reasoning is why `cap_for_llm` is worth testing at
all. `cap_for_llm` sorts by a shock magnitude, but `Candidate` originally
had no such field, so the ordering branch was unreachable from its one
real call site (`state["candidates"]`) and could only be exercised against
a duck-typed stand-in. That was not merely dead code: `MarketScan` sorts
its leaders by shock magnitude and then returns them sorted by *symbol*,
so the fallback would have capped on the alphabet -- paying to analyse the
alphabetically-first 20 of ~121 candidates every day and skipping the
largest movers. **D-130** added `Candidate.origin_sigma`, and the ordering
test below now uses real `Candidate` instances precisely so it proves
reachability from the real call site rather than from a stand-in.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from lagmatrix.adapters.llm import BatchAnalystClient, DirectAnalystClient, cap_for_llm
from lagmatrix.config import Settings, load_settings
from lagmatrix.domain.models import Candidate


class _Note(BaseModel):
    """Minimal stand-in for `QuantAnalystNote`/`DayTradeAnalystNote` (TASK-3.2,
    not yet built). `AnalystClient.classify` is generic over `schema: type[T]`,
    so this file tests the protocol without coupling to the real note shapes
    PHASE-4/5 own.
    """

    status: str = "ok"
    # An analytical field, so a test can distinguish two payloads without
    # abusing `status`. `status` is infrastructure the client writes -- a
    # stand-in whose only field is `status` forced production code to trust
    # the payload for some schemas and not others, keyed on an unrelated
    # field's presence. The fixture should follow the design, not shape it.
    reasoning: str = ""


# ---------------------------------------------------------------------------
# TASK-3.4 -- Settings / load_settings
# ---------------------------------------------------------------------------


def test_settings_model_default_is_the_haiku_id():
    """Pins TASK-3.1's default: `Settings().model` must be the Haiku id, not
    the current `claude-opus-5` (`config.py:27` today) or anything else --
    an owner decision that could silently regress with no test to catch it.

    Falsifiable by: leaving `Settings.model`'s default unchanged, or changing
    it to any string other than `"claude-haiku-4-5-20251001"`.
    """
    assert Settings().model == "claude-haiku-4-5-20251001"


def test_settings_max_llm_candidates_defaults_to_twenty():
    """Pins TASK-3.1's second default: `max_llm_candidates`, the D-92-derived
    ceiling `cap_for_llm` enforces. Same "silent regression" risk as the
    model default above -- it is a bare `int` with no other structural
    signal pinning its value.

    Falsifiable by: `Settings` omitting `max_llm_candidates`, or defaulting
    it to any value other than `20`.
    """
    assert Settings().max_llm_candidates == 20


def test_load_settings_returns_a_settings_instance():
    """TASK-3.4: `load_settings()` must actually construct `Settings`, not
    return `None` or a plain dict standing in for one.

    Falsifiable by: `load_settings` returning anything other than a
    `Settings` instance (e.g. a dict, or the class itself unconstructed).
    """
    settings = load_settings()
    assert isinstance(settings, Settings)


def test_load_settings_picks_up_an_env_override(monkeypatch):
    """TASK-3.4: `load_settings()` reads `LAGMATRIX_MODEL` from the
    environment at call time, per `Settings`'s `env_prefix="LAGMATRIX_"`
    (`config.py:9`) -- not a value baked in at import time.

    Falsifiable by: `load_settings` ignoring the environment (e.g.
    hardcoding a model, or reading `os.environ` once at import and caching
    it) -- `settings.model` would then still read the default instead of
    the override set below.
    """
    monkeypatch.setenv("LAGMATRIX_MODEL", "claude-opus-5-override-test")
    settings = load_settings()
    assert settings.model == "claude-opus-5-override-test"


# ---------------------------------------------------------------------------
# TASK-3.7 -- DirectAnalystClient
# ---------------------------------------------------------------------------


class _FakeStructuredLLM:
    """Stands in for `ChatAnthropic(...).with_structured_output(schema)`.

    Records every `messages` argument it is called with. `ainvoke` sleeps a
    fixed `delay` before returning, so the *wall-clock time to classify
    every key* is what proves concurrency (see the test below) -- unlike a
    blocking barrier, a plain `sleep` cannot be caught by a per-key
    try/except (D-38) and turned into a plausible `status="error"` note, so
    a sequential implementation cannot pass this by swallowing the proof as
    an ordinary classification failure.
    """

    def __init__(self, schema: type[BaseModel], delay: float, recorder: list):
        self.schema = schema
        self._delay = delay
        self._recorder = recorder

    async def ainvoke(self, messages):
        self._recorder.append(messages)
        await asyncio.sleep(self._delay)
        return self.schema(status="ok")


class _FakeChatAnthropic:
    """Stands in for a `ChatAnthropic` instance. `with_structured_output`
    is expected to be called once with the note schema; a fresh
    `_FakeStructuredLLM` is handed back for every call, sharing `delay` and
    the `ainvoke_calls` recorder.
    """

    def __init__(self, delay: float = 0.0):
        self.with_structured_output_calls: list[type[BaseModel]] = []
        self._delay = delay
        self.ainvoke_calls: list = []

    def with_structured_output(self, schema: type[BaseModel]):
        self.with_structured_output_calls.append(schema)
        return _FakeStructuredLLM(schema, self._delay, self.ainvoke_calls)


def _system_and_human(messages) -> tuple[SystemMessage, HumanMessage]:
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    assert len(system_msgs) == 1, "exactly one system block expected"
    assert len(human_msgs) == 1, "exactly one uncached user block expected"
    return system_msgs[0], human_msgs[0]


def _block_text(content) -> str:
    assert isinstance(content, list), "content must be a list of blocks, not a bare string"
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


async def test_direct_client_classifies_every_key_once_concurrently():
    """TASK-3.7: `.with_structured_output(schema).ainvoke(...)` is called
    exactly once per key, and the calls run concurrently (`asyncio.gather`)
    rather than one awaited to completion before the next starts.

    Concurrency is proven by wall-clock time against `_FakeStructuredLLM`'s
    fixed per-call `delay`: five keys at 0.05s each take about 0.05s total
    if gathered, and about 0.25s if awaited one at a time. The threshold
    below sits at 60% of the sequential total, comfortably above the
    concurrent time and comfortably below the sequential one.

    Falsifiable by: calling `ainvoke` fewer or more than once per key, or
    awaiting each call to completion before starting the next -- the
    latter's wall-clock time crosses the threshold below and the test
    fails on the timing assertion, not merely runs slower.
    """
    briefs = {f"K{i}": f"brief for K{i}" for i in range(5)}
    delay = 0.05
    fake_llm = _FakeChatAnthropic(delay=delay)
    client = DirectAnalystClient(fake_llm)

    start = time.monotonic()
    result = await client.classify(briefs, _Note, system_prompt="classify these")
    elapsed = time.monotonic() - start

    assert set(result) == set(briefs)
    assert len(fake_llm.ainvoke_calls) == len(briefs)
    # NOT `schema is _Note`. The client deliberately passes a STRIPPED
    # schema with `status`/`model` removed, so the model cannot write
    # the fields the client owns -- a live run had it answering
    # `status="error"` to decline. Identity held here only while the
    # stand-in was degenerate (its sole field was `status`, so stripping
    # left nothing and the passthrough fallback fired). Assert the
    # property that matters instead.
    for sent in fake_llm.with_structured_output_calls:
        props = sent.model_json_schema()["properties"]
        assert "status" not in props, "the model must not be able to write status"
        assert "reasoning" in props, "analytical fields must survive the strip"
    sequential_total = delay * len(briefs)
    assert elapsed < sequential_total * 0.6, (
        f"classify took {elapsed:.3f}s for {len(briefs)} keys at {delay}s each -- "
        "looks sequential, not concurrent"
    )


async def test_direct_client_cache_boundary_excludes_as_of_varying_content_d16():
    """TASK-3.7, the most important test in this phase: the assembled
    message list places `system_prompt` alone in a block carrying
    `cache_control: {"type": "ephemeral"}`, and each candidate's brief
    alone in a separate, uncached block. Structurally: the *other*
    candidate's as_of-varying brief text must never appear in the cached
    block, and the cached block must never carry any candidate-specific
    text at all.

    This is the test that stops a cached prefix from ever capturing
    point-in-time-varying content (D-16) -- if it did, every candidate in a
    run could be scored against a stale `as_of`, silently and plausibly.

    Falsifiable by: putting the brief (or any part of it) inside the cached
    block, by omitting `cache_control` from the system block, or by adding
    `cache_control` to the per-candidate block -- each of the assertions
    below catches a different one of those.
    """
    briefs = {
        "AAA": "Symbol AAA as_of 2026-06-01: median_ci_width=0.12 duplicate_count=0",
        "BBB": "Symbol BBB as_of 2026-06-02: median_ci_width=0.30 duplicate_count=1",
    }
    system_prompt = "You are the quant analyst. Classify replication_expectation."
    fake_llm = _FakeChatAnthropic()
    client = DirectAnalystClient(fake_llm)

    await client.classify(briefs, _Note, system_prompt=system_prompt)

    assert len(fake_llm.ainvoke_calls) == len(briefs)
    for messages in fake_llm.ainvoke_calls:
        system_msg, human_msg = _system_and_human(messages)
        cached_text = _block_text(system_msg.content)
        uncached_text = _block_text(human_msg.content)

        # the fixed instructions are in the cached block, carrying the
        # cache_control breakpoint...
        assert system_prompt in cached_text
        assert any(
            isinstance(b, dict) and b.get("cache_control") == {"type": "ephemeral"}
            for b in system_msg.content
        )
        # ...and never on the per-candidate block.
        assert not any(
            isinstance(b, dict) and b.get("cache_control") for b in human_msg.content
        )

        # exactly one brief's text is present, and it is not in the cached
        # block -- the D-16 property, asserted directly rather than implied.
        matches = [key for key, brief in briefs.items() if brief in uncached_text]
        assert matches, "no brief text found in the uncached block"
        assert len(matches) == 1, "more than one candidate's brief leaked into one call"
        for brief in briefs.values():
            assert brief not in cached_text


async def test_direct_client_key_that_raises_comes_back_status_error_never_omitted():
    """D-38, on the direct path: a key whose `ainvoke` call raises must
    still appear in the result dict, as `schema(status="error")` -- never
    silently dropped, and never allowed to sink the other keys in the same
    `classify()` call.

    Falsifiable by: letting the exception propagate out of `classify`
    (this test would then error instead of asserting), or by returning a
    dict missing the failed key.
    """

    class _FlakyStructured:
        def __init__(self, schema):
            self.schema = schema

        async def ainvoke(self, messages):
            _, human_msg = _system_and_human(messages)
            if "BOOM" in _block_text(human_msg.content):
                raise RuntimeError("simulated classification failure")
            return self.schema(status="ok")

    class _FlakyLLM:
        def with_structured_output(self, schema):
            return _FlakyStructured(schema)

    briefs = {"GOOD": "brief for GOOD", "BAD": "BOOM brief for BAD"}
    client = DirectAnalystClient(_FlakyLLM())

    result = await client.classify(briefs, _Note, system_prompt="sys")

    assert set(result) == set(briefs), "a failing key must not be omitted (D-38)"
    assert result["GOOD"].status == "ok"
    assert result["BAD"].status == "error"


# ---------------------------------------------------------------------------
# TASK-3.9 -- BatchAnalystClient
# ---------------------------------------------------------------------------


@dataclass
class _ToolUseBlock:
    type: str
    name: str
    input: dict


@dataclass
class _BatchResult:
    type: str
    message: SimpleNamespace | None = None


@dataclass
class _BatchResultItem:
    custom_id: str
    result: _BatchResult


class _FakeBatches:
    """Stands in for `anthropic.Anthropic().messages.batches`. `statuses`
    is the `processing_status` `retrieve()` reports on each successive
    call -- the last one must be `"ended"` for polling to stop.
    """

    def __init__(self, statuses: list[str], results: list[_BatchResultItem]):
        self.create_calls: list[list[dict]] = []
        self.retrieve_calls: list[str] = []
        self._statuses = statuses
        self._results = results
        self.batch_id = "batch_test_123"

    def create(self, *, requests):
        self.create_calls.append(requests)
        return SimpleNamespace(id=self.batch_id, processing_status=self._statuses[0])

    def retrieve(self, batch_id):
        self.retrieve_calls.append(batch_id)
        idx = min(len(self.retrieve_calls) - 1, len(self._statuses) - 1)
        return SimpleNamespace(id=batch_id, processing_status=self._statuses[idx])

    def results(self, batch_id):
        # Echo back the custom_ids that were actually SENT, positionally, the
        # way the real Batch API does. These fakes previously hardcoded the
        # caller's dict keys as custom_ids, which quietly asserted that
        # `custom_id == key` -- a shape the live API rejects, because
        # `candidate_key()` produces `CAND|2026-06-01` and the pattern is
        # ^[a-zA-Z0-9_-]{1,64}$. A fake that cannot express the constraint the
        # real service enforces is how that bug survived to a live 400.
        sent = [r["custom_id"] for r in self.create_calls[0]] if self.create_calls else []
        for i, item in enumerate(self._results):
            if i < len(sent):
                item = dataclasses.replace(item, custom_id=sent[i])
            yield item
        return


class _FakeAnthropic:
    def __init__(self, statuses: list[str], results: list[_BatchResultItem]):
        self.messages = SimpleNamespace(batches=_FakeBatches(statuses, results))


def _succeeded(custom_id: str, note_input: dict) -> _BatchResultItem:
    return _BatchResultItem(
        custom_id=custom_id,
        result=_BatchResult(
            type="succeeded",
            message=SimpleNamespace(
                content=[_ToolUseBlock(type="tool_use", name="note", input=note_input)]
            ),
        ),
    )


def _errored(custom_id: str) -> _BatchResultItem:
    return _BatchResultItem(custom_id=custom_id, result=_BatchResult(type="errored"))


async def test_batch_client_builds_one_request_per_key_with_custom_id():
    """TASK-3.9: one batch request per key, each with a UNIQUE `custom_id`
    that the Batch API will accept, and results mapped back to the caller's
    own keys.

    **Corrected 2026-09-12.** This previously asserted `custom_id` *is* the
    key. The live API rejects that: `candidate_key()` produces
    `CAND|2026-06-01`, and `custom_id` must match `^[a-zA-Z0-9_-]{1,64}$`, so
    the whole submission came back 400. The test was pinning a shape the
    service forbids -- corrected to describe the real contract, not relaxed.

    Falsifiable by: submitting fewer/more requests than keys, reusing one
    `custom_id` for two keys, emitting an id the API would reject, or
    returning results under the sanitised ids instead of the caller's keys.
    """
    import re
    briefs = {"AAA": "brief AAA", "BBB": "brief BBB"}
    results = [_succeeded("AAA", {"status": "ok"}), _succeeded("BBB", {"status": "ok"})]
    fake_client = _FakeAnthropic(statuses=["ended"], results=results)
    client = BatchAnalystClient(fake_client, model="claude-haiku-4-5-20251001", poll_interval=0)

    out = await client.classify(briefs, _Note, system_prompt="sys")

    assert len(fake_client.messages.batches.create_calls) == 1
    requests = fake_client.messages.batches.create_calls[0]
    assert len(requests) == len(briefs)
    ids = [r["custom_id"] for r in requests]
    assert len(set(ids)) == len(briefs), "each key needs its own custom_id"
    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    assert all(pattern.match(i) for i in ids), f"the API would reject {ids}"
    assert set(out) == set(briefs), "results must come back under the caller's keys"


async def test_batch_client_polls_retrieve_until_ended():
    """TASK-3.9: polls `.retrieve()` repeatedly while `processing_status`
    is not `"ended"`, and stops once it is -- not a single check.

    Falsifiable by: calling `retrieve` only once regardless of status (this
    test's fake reports `"in_progress"` twice before `"ended"`, so a
    single-check implementation would try to read results while the batch
    is still running), or never checking `processing_status` at all.
    """
    briefs = {"AAA": "brief AAA"}
    results = [_succeeded("AAA", {"status": "ok"})]
    fake_client = _FakeAnthropic(statuses=["in_progress", "in_progress", "ended"], results=results)
    client = BatchAnalystClient(fake_client, model="claude-haiku-4-5-20251001", poll_interval=0)

    result = await client.classify(briefs, _Note, system_prompt="sys")

    assert len(fake_client.messages.batches.retrieve_calls) >= 3
    assert result["AAA"].status == "ok"


async def test_batch_client_parses_forced_tool_use_block_by_key():
    """TASK-3.9: each succeeded result's forced-tool-use block (`type ==
    "tool_use"`, carrying `.input`) is parsed into `schema` and stored
    under that result's `custom_id`.

    Falsifiable by: returning the raw block instead of a `schema` instance,
    mixing up which result's `input` goes to which key, or reading some
    other content block instead of the tool-use one.
    """
    briefs = {"AAA": "brief AAA", "BBB": "brief BBB"}
    results = [
        _succeeded("AAA", {"reasoning": "for AAA"}),
        _succeeded("BBB", {"reasoning": "for BBB"}),
    ]
    fake_client = _FakeAnthropic(statuses=["ended"], results=results)
    client = BatchAnalystClient(fake_client, model="claude-haiku-4-5-20251001", poll_interval=0)

    result = await client.classify(briefs, _Note, system_prompt="sys")

    assert isinstance(result["AAA"], _Note)
    assert result["AAA"].reasoning == "for AAA"
    assert isinstance(result["BBB"], _Note)
    assert result["BBB"].reasoning == "for BBB"


async def test_batch_client_errored_result_comes_back_status_error_never_omitted():
    """D-38, on the batch path: a result whose `.result.type == "errored"`
    (Batch API's own failure marker, distinct from the direct path's raised
    exception) must still appear in the returned dict, as
    `schema(status="error")`.

    Falsifiable by: raising instead of returning normally, or omitting the
    errored key from the result dict.
    """
    briefs = {"GOOD": "brief GOOD", "BAD": "brief BAD"}
    results = [_succeeded("GOOD", {"status": "ok"}), _errored("BAD")]
    fake_client = _FakeAnthropic(statuses=["ended"], results=results)
    client = BatchAnalystClient(fake_client, model="claude-haiku-4-5-20251001", poll_interval=0)

    result = await client.classify(briefs, _Note, system_prompt="sys")

    assert set(result) == set(briefs), "an errored key must not be omitted (D-38)"
    assert result["GOOD"].status == "ok"
    assert result["BAD"].status == "error"


async def test_direct_and_batch_assemble_identical_cache_boundary_for_the_same_inputs():
    """TASK-3.9: proves the "one shared prompt-assembly function, not two"
    requirement as an observable behaviour rather than a private symbol --
    see the module docstring for why. Runs the same `system_prompt`/briefs
    through both clients and asserts what each actually sends its fake is
    structurally identical: the same cached system block (text and
    `cache_control`) and, per key, the same uncached per-candidate block.

    Falsifiable by: either client independently re-deriving the cached
    block or the per-candidate block in a way that drifts from the other --
    e.g. one path forgetting `cache_control`, wording the system block
    differently, or wrapping the brief text differently -- any of which
    would break the equality assertions below without necessarily breaking
    either client's own dedicated test above.
    """
    system_prompt = "You are the day-trade analyst. Classify liquidity_tier."
    briefs = {"AAA": "brief for AAA as_of 2026-06-01", "BBB": "brief for BBB as_of 2026-06-02"}

    fake_llm = _FakeChatAnthropic()
    direct = DirectAnalystClient(fake_llm)
    await direct.classify(briefs, _Note, system_prompt=system_prompt)

    direct_system_by_key: dict[str, list] = {}
    direct_user_by_key: dict[str, list] = {}
    for messages in fake_llm.ainvoke_calls:
        system_msg, human_msg = _system_and_human(messages)
        text = _block_text(human_msg.content)
        (key,) = [k for k, b in briefs.items() if b in text]
        direct_system_by_key[key] = system_msg.content
        direct_user_by_key[key] = human_msg.content

    results = [_succeeded(k, {"status": "ok"}) for k in briefs]
    fake_client = _FakeAnthropic(statuses=["ended"], results=results)
    batch = BatchAnalystClient(fake_client, model="claude-haiku-4-5-20251001", poll_interval=0)
    await batch.classify(briefs, _Note, system_prompt=system_prompt)

    # Pair by position, not by `custom_id`. Batch custom_ids are sanitised
    # (`c0`, `c1`, ...) because the API requires ^[a-zA-Z0-9_-]{1,64}$ and
    # `candidate_key()` produces `CAND|2026-06-01`; they are emitted in
    # `briefs` order, so request i belongs to key i. The drift this test
    # exists to catch is in the assembled blocks, not in the id scheme.
    requests = fake_client.messages.batches.create_calls[0]
    requests_by_key = dict(zip(briefs, requests, strict=True))

    for key in briefs:
        batch_system = requests_by_key[key]["params"]["system"]
        batch_user = requests_by_key[key]["params"]["messages"][0]["content"]
        assert batch_system == direct_system_by_key[key], (
            f"system/cached block drifted between direct and batch for {key!r}"
        )
        assert batch_user == direct_user_by_key[key], (
            f"per-candidate/uncached block drifted between direct and batch for {key!r}"
        )


# ---------------------------------------------------------------------------
# TASK-3.13 -- cap_for_llm
# ---------------------------------------------------------------------------


@dataclass
class _Scored:
    """A duck-typed stand-in for whatever `cap_for_llm` actually receives.

    Deliberately not `Candidate`: `Candidate` has no `sigma` field and,
    being a plain pydantic model, rejects assigning one (`ValueError:
    "Candidate" object has no field "sigma"`, confirmed directly against
    this repo's model before writing this test) -- see the module
    docstring's note on this gap.
    """

    symbol: str
    sigma: float | None = None


def _candidate(symbol: str, origin_sigma: float | None = None) -> Candidate:
    """Real `Candidate`, not `_Scored` -- `Candidate` now carries
    `origin_sigma` (the amendment that closed the reachability gap this
    module's docstring originally flagged), so `cap_for_llm`'s sigma-
    ordering branch can be exercised through the actual call site
    (`state["candidates"]`, `list[Candidate]`) rather than only a stand-in.
    """
    return Candidate(
        symbol=symbol,
        direction="up",
        as_of=date(2026, 1, 1),
        origin="scan",
        origin_sigma=origin_sigma,
    )


def test_cap_for_llm_orders_by_abs_sigma_descending_when_known():
    """TASK-3.12: when every candidate carries a known `origin_sigma`,
    `selected` is the top `max_n` by `abs(origin_sigma)` descending, and
    `capped` is the rest in that same order -- the split falls exactly at
    the `max_n` boundary. Uses real `Candidate` instances (via `_candidate`
    above) rather than `_Scored`, proving the branch is reachable from
    `cap_for_llm`'s one real call site.

    Falsifiable by: sorting ascending, sorting by raw (signed)
    `origin_sigma` instead of its absolute value, splitting at a boundary
    other than `max_n`, or `cap_for_llm` rejecting real `Candidate` objects
    outright (e.g. reading a `.sigma` attribute that doesn't exist on it).
    """
    candidates = [
        _candidate("A", origin_sigma=-2.0),
        _candidate("B", origin_sigma=5.0),
        _candidate("C", origin_sigma=1.0),
        _candidate("D", origin_sigma=-4.0),
    ]

    selected, capped = cap_for_llm(candidates, max_n=2)

    assert [c.symbol for c in selected] == ["B", "D"]
    assert [c.symbol for c in capped] == ["A", "C"]


def test_cap_for_llm_sorts_known_origin_sigma_first_and_unknown_falls_back_to_list_order():
    """Mixed real input: some candidates carry a known `origin_sigma` (scan
    origin) and some don't (`origin="external"`, before any shock scoring
    runs). The known ones must sort first, by `abs(origin_sigma)`
    descending; the unknown ones follow, in their original relative order --
    never compared against `None`, which would raise.

    Falsifiable by: raising `TypeError` comparing `None` to `float`, sorting
    an unknown-sigma candidate ahead of a known one, or reordering the
    unknown ones relative to each other.
    """
    candidates = [
        _candidate("E1"),
        _candidate("S1", origin_sigma=2.0),
        _candidate("E2"),
        _candidate("S2", origin_sigma=5.0),
    ]

    selected, capped = cap_for_llm(candidates, max_n=3)

    assert [c.symbol for c in selected] == ["S2", "S1", "E1"]
    assert [c.symbol for c in capped] == ["E2"]


def test_cap_for_llm_falls_back_to_candidate_list_order_when_sigma_unknown():
    """TASK-3.12's stated fallback: when sigma is not known for these
    candidates (the `origin="external"` case, before `leader_state` runs),
    the split preserves the original list order rather than picking an
    arbitrary one.

    Falsifiable by: raising, returning an empty split, or reordering the
    candidates by anything (e.g. symbol) other than their input order.
    """
    candidates = [_Scored("A"), _Scored("B"), _Scored("C"), _Scored("D")]

    selected, capped = cap_for_llm(candidates, max_n=2)

    assert [c.symbol for c in selected] == ["A", "B"]
    assert [c.symbol for c in capped] == ["C", "D"]


def test_cap_for_llm_selected_and_capped_partition_every_candidate_exactly_once():
    """General contract, either branch: no candidate is duplicated or lost
    across `(selected, capped)`, and the sizes sum to the input length.

    Falsifiable by: an implementation that drops a candidate on the
    sigma-unknown/known boundary, or that duplicates one into both halves.
    """
    candidates = [_Scored("A", sigma=1.0), _Scored("B", sigma=2.0), _Scored("C", sigma=3.0)]

    selected, capped = cap_for_llm(candidates, max_n=2)

    assert len(selected) == 2
    assert len(capped) == 1
    assert {c.symbol for c in selected} | {c.symbol for c in capped} == {"A", "B", "C"}
