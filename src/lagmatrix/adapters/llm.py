"""Claude client used by the analyst nodes: one `AnalystClient` protocol, two
implementations (`DirectAnalystClient` for `serve.py`, `BatchAnalystClient`
for `runner.py`), and one shared cache-boundary prompt assembly (D-16) so the
two paths cannot drift from each other (D-38).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Protocol, TypeVar

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, create_model

from lagmatrix.config import Settings, load_settings

T = TypeVar("T", bound=BaseModel)


def _analyst_schema(schema: type[BaseModel]) -> type[BaseModel]:
    """The schema handed to the model: `status` and `model` are
    infrastructure fields the client alone stamps, never the model's own
    judgement (found live -- a model declining to classify wrote
    `status="error"` alongside its refusal prose, indistinguishable from an
    actual infrastructure failure). Falls back to `schema` unchanged if
    stripping those two leaves no analytical fields at all.
    """
    fields = {
        name: (field.annotation, field)
        for name, field in schema.model_fields.items()
        if name not in ("status", "model")
    }
    if not fields:
        return schema
    return create_model(f"{schema.__name__}Analytical", **fields)


class AnalystClient(Protocol):
    async def classify(
        self, briefs: dict[str, str], schema: type[T], *, system_prompt: str
    ) -> dict[str, T]:
        """key -> rendered per-candidate prompt in; key -> parsed T out.

        Every key in `briefs` must appear in the result -- a key an
        implementation cannot classify comes back as `schema(status="error",
        ...)`, never omitted (D-38).
        """
        ...


def _assemble_blocks(system_prompt: str, brief: str) -> tuple[list[dict], list[dict]]:
    """The one place the D-16 cache boundary is drawn: `system_prompt` alone
    in a cached block, the per-candidate `brief` alone in an uncached one.
    Shared by both `AnalystClient` implementations so neither can drift.
    """
    system_blocks = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    user_blocks = [{"type": "text", "text": brief}]
    return system_blocks, user_blocks


class DirectAnalystClient:
    """Wraps a `ChatAnthropic` instance. Used by `serve.py`."""

    def __init__(self, llm: ChatAnthropic):
        self._llm = llm

    async def classify(
        self, briefs: dict[str, str], schema: type[T], *, system_prompt: str
    ) -> dict[str, T]:
        structured = self._llm.with_structured_output(_analyst_schema(schema))

        async def _one(brief: str) -> T:
            system_blocks, user_blocks = _assemble_blocks(system_prompt, brief)
            messages = [
                SystemMessage(content=system_blocks),
                HumanMessage(content=user_blocks),
            ]
            try:
                payload = await structured.ainvoke(messages)
            except Exception as exc:
                note = schema(status="error")
                if "reasoning" in schema.model_fields:
                    note.reasoning = f"{type(exc).__name__}: {exc}"
            else:
                data = payload.model_dump() if isinstance(payload, BaseModel) else dict(payload)
                # Gate on the field being acted upon, not a proxy for it. The
                # model never sees `status` (it is stripped from the schema it
                # is given), so anything resembling one in the payload is not
                # the client's judgement and is dropped.
                data.pop("model", None)
                if "status" in schema.model_fields:
                    data.pop("status", None)
                    note = schema(status="ok", **data)
                else:
                    note = schema(**data)
            if "model" in schema.model_fields:
                note.model = self._llm.model
            return note

        results = await asyncio.gather(*(_one(brief) for brief in briefs.values()))
        return dict(zip(briefs.keys(), results, strict=True))


class BatchAnalystClient:
    """Wraps a raw `anthropic.Anthropic` client's Message Batches API --
    submission and polling aren't exposed through `langchain_anthropic`.
    Used by `runner.py`.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        max_tokens: int = 500,
        poll_interval: float = 5.0,
        timeout: float = 600.0,
    ):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._poll_interval = poll_interval
        self._timeout = timeout

    def _build_request(
        self, custom_id: str, brief: str, schema: type[T], system_prompt: str
    ) -> dict:
        system_blocks, user_blocks = _assemble_blocks(system_prompt, brief)
        return {
            "custom_id": custom_id,
            "params": {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": system_blocks,
                "messages": [{"role": "user", "content": user_blocks}],
                "tools": [{"name": "note", "input_schema": schema.model_json_schema()}],
                "tool_choice": {"type": "tool", "name": "note"},
            },
        }

    async def classify(
        self, briefs: dict[str, str], schema: type[T], *, system_prompt: str
    ) -> dict[str, T]:
        # The Batch API requires `custom_id` to match ^[a-zA-Z0-9_-]{1,64}$.
        # `candidate_key()` produces `CAND|2026-06-01`, whose `|` and `:` are
        # both illegal, and the live endpoint rejects the WHOLE submission with
        # a 400 -- so every batch call this system could have made would have
        # failed. Found by a real API call; our fakes accept any string, which
        # is why faking could never have caught it.
        #
        # Positional ids rather than a slugged key: slugging is lossy, and two
        # distinct keys could collide into one id, which would silently return
        # one candidate's note for another. The caller gets its own keys back.
        ids = {f"c{i}": key for i, key in enumerate(briefs)}
        analytical_schema = _analyst_schema(schema)
        requests = [
            self._build_request(custom_id, briefs[key], analytical_schema, system_prompt)
            for custom_id, key in ids.items()
        ]
        batch = self._client.messages.batches.create(requests=requests)

        deadline = time.monotonic() + self._timeout
        status = batch.processing_status
        while status != "ended":
            if time.monotonic() > deadline:
                raise TimeoutError(f"batch {batch.id} did not finish within {self._timeout}s")
            await asyncio.sleep(self._poll_interval)
            batch = self._client.messages.batches.retrieve(batch.id)
            status = batch.processing_status

        results: dict[str, T] = {}
        for item in self._client.messages.batches.results(batch.id):
            if item.result.type == "succeeded":
                tool_use = next(
                    b for b in item.result.message.content if b.type == "tool_use"
                )
                data = dict(tool_use.input)
                # Same reasoning as the direct client: gate on the field being
                # acted upon. A live 6-pair run had the model answering
                # `status="error"` with prose to decline; `status` is now
                # stripped from the schema it sees, and anything resembling one
                # in the payload is dropped rather than believed.
                data.pop("model", None)
                if "status" in schema.model_fields:
                    data.pop("status", None)
                    note = schema(status="ok", **data)
                else:
                    note = schema(**data)
            else:
                note = schema(status="error")
                if "reasoning" in schema.model_fields:
                    error = getattr(item.result, "error", None)
                    note.reasoning = (
                        str(error)
                        if error is not None
                        else f"batch item errored (type={item.result.type!r})"
                    )
            if "model" in schema.model_fields:
                note.model = self._model
            results[ids[item.custom_id]] = note
        return results


def build_analyst_client(
    settings: Settings | None = None, *, mode: Literal["direct", "batch"]
) -> AnalystClient | None:
    """The one shared factory: returns `None` if no API key is configured,
    else constructs the client `mode` names. `runner.py` (`mode="batch"`)
    and `serve.py` (`mode="direct"`) call this and nothing else.
    """
    if settings is None:
        settings = load_settings()
    if not settings.anthropic_api_key:
        return None
    if mode == "direct":
        llm = ChatAnthropic(model=settings.model, anthropic_api_key=settings.anthropic_api_key)
        return DirectAnalystClient(llm)
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return BatchAnalystClient(client, model=settings.model)


def cap_for_llm(candidates: list, max_n: int) -> tuple[list, list]:
    """Splits `candidates` into `(selected, capped)` at `max_n`, ordered by
    `abs(origin_sigma)` descending when known, falling back to input list
    order otherwise (`origin="external"` candidates carry no shock sigma
    until `leader_state` runs).
    """

    def key(candidate: Any) -> tuple[int, float]:
        sigma = getattr(candidate, "origin_sigma", None)
        if sigma is None:
            return (1, 0.0)
        return (0, -abs(sigma))

    ordered = sorted(candidates, key=key)
    return ordered[:max_n], ordered[max_n:]
