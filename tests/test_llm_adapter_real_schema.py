"""RED for a defect in already-committed PHASE-3 code
(`lagmatrix.adapters.llm`): `test_llm_adapter.py` proves the D-38 error-path
contract and the `AnalystClient` protocol only against a local stand-in

    class _Note(BaseModel):
        status: str = "ok"

which constructs trivially with `status="error"` alone. Against the *real*
note models (`QuantAnalystNote`, `DayTradeAnalystNote` -- both `status`,
`reasoning`, `model`, and a classification field all required, no defaults)
`schema(status="error")` inside `DirectAnalystClient`/`BatchAnalystClient`'s
except/error branches raises `pydantic.ValidationError` from *inside* the
error handler, so the exception this branch exists to catch turns into a
different, worse one that escapes `classify` entirely -- the exact case
D-38 promises never happens.

Separately, neither client stamps `note.model`: `DirectAnalystClient` returns
`structured.ainvoke(...)`'s result unmodified, and `BatchAnalystClient` does
`schema(**tool_use.input)`, so `model` is whatever the LLM chose to write
about itself in a JSON payload -- untrustworthy, and fatal to PHASE-10's
purpose of comparing Haiku against Opus if the two get scrambled.

This file imports the *real* `QuantAnalystNote`/`DayTradeAnalystNote` from
`lagmatrix.domain.models` -- that substitution is the whole point. Nothing
here makes a real network call; every `ChatAnthropic`/`anthropic.Anthropic`
stand-in is a local fake, following `test_llm_adapter.py`'s style.

Do not modify `tests/test_llm_adapter.py` or any implementation file from
this one -- see AGENTS/CLAUDE.md's TDD-red contract.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from lagmatrix.adapters.llm import BatchAnalystClient, DirectAnalystClient
from lagmatrix.domain.models import DayTradeAnalystNote, QuantAnalystNote

CONFIGURED_MODEL = "claude-haiku-4-5-20251001"


def _system_and_human(messages) -> tuple[SystemMessage, HumanMessage]:
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    return system_msgs[0], human_msgs[0]


def _block_text(content) -> str:
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


# ---------------------------------------------------------------------------
# Fakes -- mirror test_llm_adapter.py's style, extended with a `.model`
# attribute so the direct path has something to stamp notes from.
# ---------------------------------------------------------------------------


class _FakeStructuredLLMReturning:
    """Returns a fixed, pre-built note for every call -- used to prove the
    client overwrites whatever `model` value the note arrives with."""

    def __init__(self, note):
        self._note = note

    async def ainvoke(self, messages):
        return self._note


class _FakeStructuredLLMRaisingFor:
    """Raises for any brief containing `trigger`; otherwise returns `ok_note`."""

    def __init__(self, ok_note, trigger: str):
        self._ok_note = ok_note
        self._trigger = trigger

    async def ainvoke(self, messages):
        _, human_msg = _system_and_human(messages)
        if self._trigger in _block_text(human_msg.content):
            raise RuntimeError("simulated classification failure")
        return self._ok_note


class _FakeChatAnthropic:
    """Stands in for `ChatAnthropic`, carrying `.model` -- the real class's
    own attribute (confirmed against `langchain_anthropic.ChatAnthropic`) --
    since `DirectAnalystClient` is constructed from an already-configured
    `llm`, not a bare model string."""

    def __init__(self, model: str, structured_llm):
        self.model = model
        self._structured_llm = structured_llm

    def with_structured_output(self, schema):
        return self._structured_llm


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


class _FakeBatches:
    def __init__(self, results: list[_BatchResultItem]):
        self._results = results
        self.batch_id = "batch_test_123"
        self.create_calls: list[list[dict]] = []

    def create(self, *, requests):
        self.create_calls.append(requests)
        return SimpleNamespace(id=self.batch_id, processing_status="ended")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

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
    def __init__(self, results: list[_BatchResultItem]):
        self.messages = SimpleNamespace(batches=_FakeBatches(results))


# ---------------------------------------------------------------------------
# 1/2 -- the D-38 error path against the real note models
# ---------------------------------------------------------------------------


async def test_direct_client_error_path_survives_the_real_quant_analyst_note_schema():
    """`DirectAnalystClient.classify` against the real `QuantAnalystNote`
    (four required fields beyond `status`, no defaults): a key whose
    `ainvoke` raises must still come back as a valid `status="error"` note,
    and no exception may escape `classify`.

    Falsifiable by: `classify` raising `pydantic.ValidationError` (today's
    behaviour -- `schema(status="error")` inside the except block fails
    because `replication_expectation`/`flagged_concerns`/`reasoning`/`model`
    are all required), or by the failing key being missing from the result.
    """
    ok_note = QuantAnalystNote(
        status="ok",
        replication_expectation="high",
        flagged_concerns=[],
        reasoning="fine",
        model=CONFIGURED_MODEL,
    )
    structured = _FakeStructuredLLMRaisingFor(ok_note, trigger="BOOM")
    llm = _FakeChatAnthropic(CONFIGURED_MODEL, structured)
    client = DirectAnalystClient(llm)
    briefs = {"GOOD": "brief for GOOD", "BAD": "BOOM brief for BAD"}

    result = await client.classify(briefs, QuantAnalystNote, system_prompt="sys")

    assert set(result) == set(briefs), "a failing key must not be omitted (D-38)"
    assert result["GOOD"].status == "ok"
    assert isinstance(result["BAD"], QuantAnalystNote)
    assert result["BAD"].status == "error"


async def test_batch_client_error_path_survives_the_real_day_trade_analyst_note_schema():
    """`BatchAnalystClient.classify` against the real `DayTradeAnalystNote`:
    an `.result.type == "errored"` batch item must still come back as a
    valid `status="error"` note, not raise.

    Falsifiable by: `classify` raising `pydantic.ValidationError` (today's
    behaviour -- same missing-required-fields failure as the direct path,
    on `BatchAnalystClient`'s `schema(status="error")` branch instead), or
    by the errored key being missing from the result.
    """
    results = [
        _succeeded(
            "GOOD",
            {
                "status": "ok",
                "liquidity_tier": "ample",
                "gap_dominant": False,
                "reasoning": "fine",
                "model": CONFIGURED_MODEL,
            },
        ),
        _errored("BAD"),
    ]
    fake_client = _FakeAnthropic(results)
    client = BatchAnalystClient(fake_client, model=CONFIGURED_MODEL, poll_interval=0)
    briefs = {"GOOD": "brief GOOD", "BAD": "brief BAD"}

    result = await client.classify(briefs, DayTradeAnalystNote, system_prompt="sys")

    assert set(result) == set(briefs), "an errored key must not be omitted (D-38)"
    assert result["GOOD"].status == "ok"
    assert isinstance(result["BAD"], DayTradeAnalystNote)
    assert result["BAD"].status == "error"


# ---------------------------------------------------------------------------
# 3 -- `model` is stamped by the client, never trusted from the payload
# ---------------------------------------------------------------------------


async def test_direct_client_stamps_its_own_configured_model_not_the_notes_claim():
    """The note `DirectAnalystClient` hands back must carry the model the
    client was actually configured with (`llm.model`), never whatever the
    LLM's structured-output payload claims about itself.

    Falsifiable by: `classify` returning `structured.ainvoke(...)`'s result
    unmodified (today's behaviour) -- the assertion below would then read
    the fake's lie, `"claude-3-opus-not-really"`, instead of
    `CONFIGURED_MODEL`.
    """
    lying_note = QuantAnalystNote(
        status="ok",
        replication_expectation="high",
        flagged_concerns=[],
        reasoning="fine",
        model="claude-3-opus-not-really",
    )
    structured = _FakeStructuredLLMReturning(lying_note)
    llm = _FakeChatAnthropic(CONFIGURED_MODEL, structured)
    client = DirectAnalystClient(llm)

    result = await client.classify({"AAA": "brief"}, QuantAnalystNote, system_prompt="sys")

    assert result["AAA"].model == CONFIGURED_MODEL


async def test_batch_client_stamps_its_own_configured_model_not_the_notes_claim():
    """Same property on the batch path: the note's `model` must be the
    model `BatchAnalystClient` was constructed with, never the value
    parsed out of the tool-use payload.

    Falsifiable by: `classify` returning `schema(**tool_use.input)`
    unmodified (today's behaviour) -- the assertion below would then read
    the fake payload's lie instead of `CONFIGURED_MODEL`.
    """
    results = [
        _succeeded(
            "AAA",
            {
                "status": "ok",
                "liquidity_tier": "ample",
                "gap_dominant": True,
                "reasoning": "fine",
                "model": "claude-3-opus-not-really",
            },
        )
    ]
    fake_client = _FakeAnthropic(results)
    client = BatchAnalystClient(fake_client, model=CONFIGURED_MODEL, poll_interval=0)

    result = await client.classify({"AAA": "brief"}, DayTradeAnalystNote, system_prompt="sys")

    assert result["AAA"].model == CONFIGURED_MODEL


# ---------------------------------------------------------------------------
# 4 -- `model` is populated on error notes too, not just successful ones
# ---------------------------------------------------------------------------


async def test_direct_client_stamps_model_on_the_error_note_too():
    """A key that fails on the direct path must still be attributable to
    the model that was being called -- `result["BAD"].model` must equal
    the configured model, not be left empty.

    Falsifiable by: an error note whose `model` is `""` (the default under
    the "everything but `status` optional" shape) instead of
    `CONFIGURED_MODEL`.
    """
    ok_note = QuantAnalystNote(
        status="ok",
        replication_expectation="high",
        flagged_concerns=[],
        reasoning="fine",
        model=CONFIGURED_MODEL,
    )
    structured = _FakeStructuredLLMRaisingFor(ok_note, trigger="BOOM")
    llm = _FakeChatAnthropic(CONFIGURED_MODEL, structured)
    client = DirectAnalystClient(llm)

    result = await client.classify(
        {"BAD": "BOOM brief"}, QuantAnalystNote, system_prompt="sys"
    )

    assert result["BAD"].model == CONFIGURED_MODEL


async def test_batch_client_stamps_model_on_the_error_note_too():
    """Same property for an errored batch item: `result["BAD"].model` must
    equal the model `BatchAnalystClient` was constructed with, not be left
    empty.

    Falsifiable by: an error note whose `model` is `""` instead of
    `CONFIGURED_MODEL`.
    """
    fake_client = _FakeAnthropic([_errored("BAD")])
    client = BatchAnalystClient(fake_client, model=CONFIGURED_MODEL, poll_interval=0)

    result = await client.classify(
        {"BAD": "brief BAD"}, DayTradeAnalystNote, system_prompt="sys"
    )

    assert result["BAD"].model == CONFIGURED_MODEL


async def test_error_note_reasoning_names_the_failure_rather_than_being_empty():
    """An error note must say *what* failed, not merely that something did.

    **This is a regression test, not a specification** — the behaviour was
    implemented before it was pinned, which is the wrong order and is recorded
    here rather than hidden. It is kept because the behaviour matters: D-119
    exists in this repo because a failure reported the wrong cause and sent the
    search after a network problem while a credential file was unreadable. An
    error note carrying `reasoning=""` is that defect again — a failure that
    says nothing, reaching an operator who then has nowhere to look.

    Falsifiable by: dropping the `note.reasoning = f"{type(exc).__name__}: ..."`
    assignment in `DirectAnalystClient.classify`'s except branch, which returns
    `reasoning=""` from the model default. Verified by mutation, not assumed —
    removing that line makes this test fail and leaves every other test in this
    file passing, which is precisely why it is worth having.
    """
    ok_note = QuantAnalystNote(
        status="ok",
        replication_expectation="high",
        flagged_concerns=[],
        reasoning="fine",
        model=CONFIGURED_MODEL,
    )
    structured = _FakeStructuredLLMRaisingFor(ok_note, trigger="BOOM")
    llm = _FakeChatAnthropic(CONFIGURED_MODEL, structured)
    client = DirectAnalystClient(llm)

    result = await client.classify(
        {"GOOD": "brief for GOOD", "BAD": "BOOM brief for BAD"},
        QuantAnalystNote,
        system_prompt="sys",
    )

    assert result["BAD"].status == "error"
    assert result["BAD"].reasoning, (
        "an error note with empty reasoning is a failure that says nothing"
    )
    # Names the failure, not just its existence.
    assert "RuntimeError" in result["BAD"].reasoning


async def test_batch_custom_ids_match_anthropics_required_pattern():
    """Batch `custom_id` must match `^[a-zA-Z0-9_-]{1,64}$`.

    Found by the real API, not by a fake. `candidate_key()` produces
    `CAND|2026-06-01`; the `|` and `:` are illegal, and the live Batch
    endpoint rejects the whole submission with HTTP 400:

        requests.0.custom_id: String should match pattern '^[a-zA-Z0-9_-]{1,64}$'

    So every batch call this system could ever have made would have failed.
    No amount of faking would have caught it -- our own fakes accept any
    string as a `custom_id`, which is precisely why TASK-7.5 exists.

    Falsifiable by: passing `candidate_key`-shaped keys straight through as
    `custom_id`, or by returning results keyed by the sanitised id rather than
    mapping back to the caller's original key.
    """
    import re

    captured: dict = {}

    class _FakeBatches:
        def create(self, *, requests):
            captured["requests"] = requests
            return SimpleNamespace(id="batch_1", processing_status="ended")

        def retrieve(self, _id):
            return SimpleNamespace(id="batch_1", processing_status="ended")

        def results(self, _id):
            note = {"status": "ok", "reasoning": "fine", "model": "x"}
            for r in captured["requests"]:
                yield SimpleNamespace(
                    custom_id=r["custom_id"],
                    result=SimpleNamespace(
                        type="succeeded",
                        message=SimpleNamespace(content=[
                            SimpleNamespace(type="tool_use", name="note", input=note)
                        ]),
                    ),
                )

    client = BatchAnalystClient(
        SimpleNamespace(messages=SimpleNamespace(batches=_FakeBatches())),
        model=CONFIGURED_MODEL,
        poll_interval=0,
    )
    briefs = {"CAND|2026-06-01": "brief a", "CAND2|2026-06-01": "brief b"}

    out = await client.classify(briefs, QuantAnalystNote, system_prompt="sys")

    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    for r in captured["requests"]:
        assert pattern.match(r["custom_id"]), (
            f"custom_id {r['custom_id']!r} would be rejected by the Batch API"
        )
    # And the caller still gets its own keys back, not the sanitised ones.
    assert set(out) == set(briefs)
