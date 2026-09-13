# PLAN-2026-09-11-quant-daytrade-perspectives

## Revision 2 (2026-09-11): Haiku by default, batch vs. direct calls, the demo gets real egress

Three owner decisions land in this revision, and one of them changes the
node architecture, not just a config value.

**1. Default model is `claude-haiku-4-5-20251001`, not Opus 5.** Real
pricing: Opus 5 $5/$25 per MTok, Sonnet 5 $2/$10, **Haiku 4.5 $1/$5**
([eesel AI](https://www.eesel.ai/blog/claude-opus-5-pricing),
[pecollective's rate table](https://pecollective.com/tools/anthropic-api-pricing/)).
The work both analyst nodes do — classify a short, pre-assembled brief into
closed `Literal` enums under a fixed `max_tokens` — is exactly the "simple
task" tier Anthropic's own model guidance names for Haiku; every hard
computation (Fisher intervals, split-half agreement, liquidity medians) is
already done deterministically before the model sees anything. `Settings.model`'s
default changes accordingly. PHASE-10 (renumbered from PHASE-9) gains a third
arm: does Opus beat Haiku by enough to justify its cost, measured on the same
sample rather than assumed either way.

**2. Batch API for the nightly path, direct calls for the demo — and this
split is architectural, not a config flag.** Tracing how LangGraph's fan-in
actually works here (checked against this repo's own `context_fusion`, not
assumed): `quant_perspective`/`day_trade_perspective` are `Send` targets, so
each runs once per candidate. But `quant_analyst`/`day_trade_analyst`, reached
only by a plain `add_edge` from those, are **not** re-invoked per candidate —
exactly like `context_fusion` sitting downstream of `leader_state`, they run
**once per graph invocation**, gathering every candidate's perspective into
one call. That was true of the first draft's design too; it just wasn't
stated correctly there (PHASE-4/5 said "reads `state["candidate"]`," singular,
which was wrong). Getting this right matters because it is *why* the Batch
API fits without restructuring the graph: the gather node already sees every
candidate in the run in one invocation, so it can submit them as one Anthropic
Message Batch instead of looping. The two paths get two different
implementations of one shared interface (`AnalystClient.classify`) — see
PHASE-3 — never two different graph topologies.

**3. The live demo pod gets real internet egress, and the `await-netpol`
guard follows it there.** The owner overruled the scoping both the team lead
and I had defaulted to. I widen `lagmatrix-web-egress` to the exact shape the
ingest policy already uses (`0.0.0.0/0` on 443 except pod/service/LAN CIDRs)
rather than inventing a narrower one — consistency with an existing,
already-reviewed rule is worth more here than a cleverer one-off, and it's
the same reasoning D-121's own comments already argue for elsewhere in this
file. On `await-netpol`: **I'm adding it to the web Deployment**, reversing
the "Deployment is fine without it" default this repo's own comments suggest
for that workload class. Two things changed the calculus enough to say so:
the web pod's `strategy: {type: Recreate}` (`infra/k8s/21-lagmatrix-web.yaml`)
means it is **fully torn down and recreated on every deploy of this very
change**, not just on rare node failure — closer to the ingest's restart
frequency than "starts once and lives" — and this plan is the first time
that pod holds *both* a live database credential and genuine outbound
internet access, which is a materially larger blast radius during the
unguarded pod-add window than it had before. The guard is a straight copy of
the ingest's own init container (same probe targets — the widened policy
gives the web pod the identical allow/except shape, so `arangodb:8529` stays
the ALLOWED probe and the LAN `:6443` target stays DENIED) — zero new logic,
reused because it already exists and works.

**What this means for where `llm=` is constructed.** The first draft scoped
`llm=` construction to `runner.py` only; that's now wrong for the reason
above. Both `runner.py` and `serve.py` call one shared factory
(`adapters/llm.py::build_analyst_client`), which returns `None` if no API
key is configured — `serve.py` degrades exactly the way it already does when
`arango_db()` returns `None` (D-117's idiom: the page still serves, the
GraphRAG-shaped section just says "unavailable" instead of failing whole).
No construction logic is duplicated between the two call sites.

**One correction to my own PHASE-3 secret spec from the first draft:** it
named the k8s secret key `ANTHROPIC_API_KEY`. That's wrong for how this
plan actually reads it — `Settings` has `env_prefix="LAGMATRIX_"`, so the
key `build_llm`/`build_analyst_client` actually consume is
`LAGMATRIX_ANTHROPIC_API_KEY`, matching the `LAGMATRIX_PG_*` secret's
naming, not `alpaca-credentials`' bare-name convention (which is read
directly by scripts via `os.environ`, bypassing `Settings` entirely — a
different pattern this plan doesn't use). Fixed in PHASE-9 below before it
became a real deploy-time bug.

Everything else from Revision 1 stands: deterministic rules underneath,
structured scoreable output, D-34 enforced at the schema level via closed
enums with no float field, the `minute.parquet` exclusion (independently
verified by the team lead), PHASE-11's honesty that day-trade's LLM layer
does not make it validatable.

**Decisions this depends on:** unchanged from Revision 1, plus: nothing new
in the decision log yet describes Batch API or the web pod's widened egress
— both should get a `spike-log` entry once this plan executes, since they're
exactly the kind of "why does this pod reach the internet now" fact a future
reader needs and won't find by reading code.

## Design question — answer unchanged from Revision 1, cost model changed

Rules first, LLM reasons over the assembled evidence, nothing calibrated
escapes into `odds_adjustment`. What changed is *which* model is the default
and how the same "does this add anything" measurement extends to compare
models, not the shape of the decision itself.

## What each perspective can actually say — unchanged from Revision 1

Quant: Fisher CI, duplicate flag, split-half stability, ETF flag,
deterministic (PHASE-1); `quant_analyst` classifies
`replication_expectation` over those numbers. Day-trade: trailing liquidity
and gap ratio from `data/bars.parquet`, deterministic (PHASE-2);
`day_trade_analyst` classifies `liquidity_tier`/`gap_dominant`, told the
`not_measurable` gaps explicitly rather than left to guess at them.
`data/minute.parquet` stays excluded — a frozen one-off extract for 98
historical fires with no nightly refresh, confirmed independently by both of
us.

## Success criteria

1. `Assessment` carries four new fields: `quant`, `day_trade` (deterministic)
   and `quant_analyst`, `day_trade_analyst` (LLM-backed). None is read by
   `assess()`'s verdict/`effective_evidence`/`odds_adjustment` logic.
2. Both analyst nodes call Claude only through `runtime.context.llm: AnalystClient
   | None`; `None` produces a fully-formed `status="not_run"` note for every
   candidate, never a missing field, in both `runner.py` and `serve.py`.
3. `runner.py` constructs a `BatchAnalystClient`; `serve.py` constructs a
   `DirectAnalystClient`. **The two are never swapped** — this is stated as
   an explicit constraint, not left implicit, because Batch's asynchronous,
   up-to-24h-latency profile would break the live SSE demo if someone later
   "optimised" it onto batch, and Direct's per-call cost would be paid 40x
   more often than necessary if the nightly path used it.
4. Every Claude call carries `max_tokens=500` and a `cache_control` breakpoint
   after the fixed system/instructions block and before the per-candidate
   evidence — never inside it, so no `as_of`-varying content is ever part of
   a cached prefix (D-16).
5. `uv run pytest` green end to end after every phase, zero tests requiring
   network by default; any real-API test is gated on `ANTHROPIC_API_KEY` /
   `LAGMATRIX_ANTHROPIC_API_KEY` being set, mirroring `LAGMATRIX_REQUIRE_LIVE`
   (Q-43). `uv run ruff check` clean.
6. `capture_baseline.py --synthetic` / `tests/test_synthetic_baseline.py`
   pass unmodified.
7. PHASE-9 and PHASE-10 record real measured tokens/latency/cost from one
   live run on each path, against the computed ceilings below.
8. PHASE-10's three pre-registered comparisons (rule vs. population, Haiku
   vs. rule, Opus vs. Haiku) are reported verbatim, including any null.
9. `lagmatrix-web-egress` is widened to exactly the ingest policy's shape,
   with the reasoning written into the manifest; `await-netpol` is added to
   the web Deployment with its own justification recorded there.

## PHASE-1 — QuantPerspective: deterministic model and function

Unchanged from Revision 1. **Completion criterion:** `uv run pytest
tests/test_quant_perspective.py -v` green; nothing wired into the graph yet.

- TASK-1.1 (test) `tests/test_quant_perspective.py`, red first: assert
  `confidence_interval`/`duplicate_flag` are called from `lagmatrix.comovement`,
  not re-derived (DRY, checked by import or monkeypatch-and-count).
- TASK-1.2 (test) red: split-half stability on a synthetic trailing window
  (reusing the `closes` fixture's shape) where one pair flips sign between
  halves and one stays stable; assert the two are distinguished. Docstring
  states this is not D-93's discovery/validation split (years) — it's the
  cheaper thing that runs inside one daily invocation.
- TASK-1.3 (test) red: candidate symbol present in an `excluded-etfs.csv`
  stand-in → `candidate_is_etf=True`.
- TASK-1.4 (impl) `QuantPerspective` in `domain/models.py`: `n_edges: int`,
  `median_ci_width: float | None`, `duplicate_count: int`,
  `split_half_sign_agree_pct: float | None`, `candidate_is_etf: bool`,
  `note: str`.
- TASK-1.5 (impl) `src/lagmatrix/graph/nodes/quant_perspective.py`:
  `compute_quant_perspective(candidate, lag_edges, closes, trail,
  excluded_etfs) -> QuantPerspective`, over `relation == "correlation"` edges
  only.

## PHASE-2 — DayTradePerspective: deterministic model and function

Unchanged from Revision 1. **Completion criterion:** `uv run pytest
tests/test_day_trade_perspective.py -v` green; nothing wired in yet.

- TASK-2.1 (test) red: new `bars_ohlcv` fixture in `tests/conftest.py`,
  long-form, matching `data/bars.parquet`'s exact columns; assert
  `median_dollar_vol`/`median_trade_count` use only sessions strictly before
  `as_of` (D-16).
- TASK-2.2 (test) red: gap-vs-intraday ratio distinguishes an all-gap
  synthetic symbol from an all-intraday one; docstring states this describes
  history only (D-96's caveat discipline).
- TASK-2.3 (test) red: `bars=None` or symbol absent from `bars` → liquidity
  fields `None` with a stated reason (D-38), run still completes.
- TASK-2.4 (test) red: `not_measurable` always contains `{"spread",
  "slippage", "borrow"}` with a reason, unconditionally.
- TASK-2.5 (impl) `DayTradePerspective` in `domain/models.py`:
  `median_dollar_vol: float | None`, `median_trade_count: float | None`,
  `n_sessions: int`, `gap_ratio: float | None`, `not_measurable:
  list[dict[str, str]]`, `note: str`.
- TASK-2.6 (impl) `src/lagmatrix/graph/nodes/day_trade_perspective.py`:
  `compute_day_trade_perspective(candidate, bars, trail) ->
  DayTradePerspective`.

## PHASE-3 — `Settings`, the two analyst-note models, and the `AnalystClient` split

**Completion criterion:** `uv run pytest tests/test_llm_adapter.py -v`
green; no test in that file makes a real network call.

- TASK-3.1 (impl) `Settings.model` default becomes
  `"claude-haiku-4-5-20251001"`. `Settings` gains
  `max_llm_candidates: int = 20` (unchanged rationale from Revision 1: D-92
  sampled 44-247 movers/date, mean ~121; 20 covers a below-average day fully
  and bounds a busy day's spend to a fixed ceiling rather than scaling with
  whatever a given day produces — applies identically whether the batch or
  the demo path is asking).
- TASK-3.2 (impl) `QuantAnalystNote` / `DayTradeAnalystNote` in
  `domain/models.py` — unchanged shape from Revision 1: closed `Literal`
  enums only for the classificatory field, no `float`.
  `QuantAnalystNote`: `status: Literal["ok", "not_run", "error"]`,
  `replication_expectation: Literal["high", "low", "insufficient_data"] |
  None`, `flagged_concerns: list[str]`, `reasoning: str`, `model: str`.
  `DayTradeAnalystNote`: `status`, `liquidity_tier: Literal["ample",
  "marginal", "thin", "insufficient_data"] | None`, `gap_dominant: bool |
  None`, `reasoning: str`, `model: str`.
- TASK-3.3 (impl) `runner.py`'s `_ALLOWED_MODELS` gains both (D-44).
- TASK-3.4 (test) red: `load_settings()` returns a `Settings` instance and
  picks up an env override (`monkeypatch.setenv("LAGMATRIX_MODEL", ...)`).
- TASK-3.5 (impl) `load_settings() -> Settings: return Settings()`.
- TASK-3.6 (design) `adapters/llm.py` defines one interface both call sites
  share:
  ```python
  class AnalystClient(Protocol):
      async def classify(
          self, briefs: dict[str, str], schema: type[T], *, system_prompt: str
      ) -> dict[str, T]:
          """key -> rendered per-candidate prompt in; key -> parsed T out.
          Every key in `briefs` must appear in the result — a key an
          implementation cannot classify comes back as `schema(status="error",
          ...)`, never omitted (D-38)."""
  ```
- TASK-3.7 (test) red, no network: a fake underlying SDK object proves
  `DirectAnalystClient` calls `ChatAnthropic.with_structured_output(schema).
  ainvoke(...)` once per key, concurrently (`asyncio.gather`), and that the
  assembled message list places `system_prompt` in a `system` block carrying
  `cache_control: {"type": "ephemeral"}` and the per-candidate brief in a
  **separate, uncached** user-turn block — a string-containment/structure
  assertion, not a network call. This is the one test that directly proves
  "the cache boundary never captures point-in-time-varying content" (D-16):
  assert the `as_of`/candidate-specific text is absent from the cached
  block and present only in the uncached one.
- TASK-3.8 (impl) `DirectAnalystClient(llm: ChatAnthropic)` implementing
  TASK-3.6's protocol, per TASK-3.7. Used by `serve.py`.
- TASK-3.9 (test) red, no network: a fake raw `anthropic.Anthropic` client
  (fake `.messages.batches.create/.retrieve/.results`) proves
  `BatchAnalystClient` builds one batch request per key with a `custom_id`,
  polls `retrieve` until `processing_status == "ended"`, and parses each
  result's forced-tool-use block into `schema` by key — and that the **same**
  system-prompt/cache_control/per-candidate-brief split from TASK-3.7 is used
  here too (one shared prompt-assembly function, not two independently
  drifting ones — DRY, and the thing this test exists to catch is exactly
  that kind of drift).
- TASK-3.10 (impl) `BatchAnalystClient(client, model, max_tokens=500,
  poll_interval=5.0, timeout=600.0)` implementing TASK-3.6's protocol, using
  the raw `anthropic` SDK (Batch submission/polling is not exposed through
  `langchain_anthropic.ChatAnthropic`). Used by `runner.py`.
- TASK-3.11 (impl) `adapters/llm.py::build_analyst_client(settings=None, *,
  mode: Literal["direct", "batch"]) -> AnalystClient | None` — the one shared
  factory: returns `None` if `settings.anthropic_api_key` is unset, else
  constructs the client `mode` names. Both `runner.py` (`mode="batch"`) and
  `serve.py` (`mode="direct"`) call this and nothing else — no duplicated
  "is a key configured" branch.
- TASK-3.12 (impl) `adapters/llm.py::cap_for_llm(candidates, max_n) ->
  tuple[list, list]` — splits a candidate list into `(selected, capped)`,
  ordered by `abs(sigma)` descending when a shock value is known for the
  candidate at this point in the graph, falling back to candidate-list order
  otherwise (`origin="external"` candidates carry no shock sigma until
  `leader_state` runs, which is a separate branch from the one computing
  this — state the caveat rather than silently picking an order). Shared by
  both analyst nodes (PHASE-4/5) rather than duplicated.
- TASK-3.13 (test) red for TASK-3.12's ordering and the fallback case.

## PHASE-4 — `quant_analyst`: a gather node, not a per-candidate one

**Completion criterion:** `uv run pytest tests/test_quant_analyst.py -v`
green, using an injected fake `AnalystClient`, no network.

Corrects Revision 1's contract: this node is reached only by a plain
`add_edge` from `quant_perspective` (a `Send` target), so — exactly like
`context_fusion` sitting downstream of `leader_state` — it runs **once per
graph invocation**, receiving the fully-merged `state["quant_by_key"]` (every
candidate in the run) and `state["candidates"]`, not a single branch's slice.

- TASK-4.1 (test) red: with a fake `AnalystClient` returning one canned note
  per key, `analyse_quant` returns `{"quant_analyst_by_key": {...}}` with one
  entry per candidate that reached `quant_perspective`.
- TASK-4.2 (test) red: `runtime.context.llm is None` → every candidate gets
  `QuantAnalystNote(status="not_run", reasoning="no LLM configured", ...)`.
- TASK-4.3 (test) red: more candidates than `runtime.context.max_llm_candidates`
  → the excess get `status="not_run", reasoning="batch cap reached (N)"`
  (via TASK-3.12's `cap_for_llm`), the rest get real notes from the fake
  client — never a candidate silently missing either field (D-38).
- TASK-4.4 (test) red: the brief built for each candidate contains its CI
  width, duplicate flag, split-half percentage and ETF flag literally
  (string-containment) — the model is handed the numbers, not raw
  `Evidence.detail` prose to re-derive them from.
- TASK-4.5 (impl) `src/lagmatrix/graph/nodes/quant_perspective.py` (same
  file PHASE-1 defined) gains `async def analyse_quant(state, runtime) ->
  dict`, calling `runtime.context.llm.classify(briefs, QuantAnalystNote,
  system_prompt=QUANT_SYSTEM_PROMPT)`.

## PHASE-5 — `day_trade_analyst`: the same gather-node shape

**Completion criterion:** `uv run pytest tests/test_day_trade_analyst.py -v`
green, same approach as PHASE-4, no network.

- TASK-5.1 through TASK-5.4 (test/impl): same shape as TASK-4.1/4.2/4.3/4.5
  for `day_trade_by_key` / `DayTradeAnalystNote`.
- TASK-5.5 (test) red: the brief for each candidate includes
  `median_dollar_vol`, `median_trade_count`, `gap_ratio`, **and every entry
  of `not_measurable` with its reason, verbatim** — the one task in this
  plan that directly enforces "the model is told what it cannot know," so it
  is tested, not assumed.

## PHASE-6 — Wire everything into the graph

**Completion criterion:** `uv run pytest tests/test_quant_wiring.py
tests/test_day_trade_wiring.py tests/test_graph_builder.py
tests/test_synthetic_baseline.py -v` green; full `uv run pytest` green, with
`test_assessment_is_never_calibrated` (D-34) unmodified and still passing.

Topology (unchanged from Revision 1's diagram — the correction in this
revision is what each node *does* internally, not the edges):

```
route_on_neighbourhood --Send--> quant_perspective --> quant_analyst -----+
route_on_neighbourhood --Send--> day_trade_perspective --> day_trade_analyst -+-> assessor
route_on_neighbourhood --Send--> leader_state ------------------------------>|
route_on_neighbourhood --Send--> vector_retriever (if with_news) -> context_fusion -+
```

- TASK-6.1 (test) red: the four new node names and four new `Assessment`
  fields are absent before this phase.
- TASK-6.2 (test) red, single candidate, `llm=None`: `assessments[0].quant`
  populated; `assessments[0].quant_analyst.status == "not_run"`; same for
  day-trade.
- TASK-6.3 (test) red, single candidate, fake `AnalystClient` injected via a
  `run_graph` kwarg: `assessments[0].quant_analyst.status == "ok"` and
  matches the fake's canned note.
- TASK-6.4 (test) red, two-candidate fan-out (CAND/CAND2): each assessment's
  four new fields reflect only its own candidate — no cross-attribution
  (D-89's exact failure class), now checked with real multi-candidate state
  reaching the gather nodes together, which is the scenario Revision 1's
  mis-stated per-candidate contract would have hidden.
- TASK-6.5 (impl) `LagMatrixState` gains `quant_by_key`, `day_trade_by_key`,
  `quant_analyst_by_key`, `day_trade_analyst_by_key`, all
  `Annotated[dict[str, T], _merge]`.
- TASK-6.6 (impl) `LagMatrixContext` gains `llm: AnalystClient | None = None`
  and `max_llm_candidates: int | None = None`.
- TASK-6.7 (impl) `builder.py`: add all four nodes;
  `analyse_quant`/`analyse_day_trade` get `retry_policy=RetryPolicy(max_attempts=3),
  timeout=30.0` (mirrors `vector_retriever` — same class of "external call
  that can fail transiently" the news node already handles). Add
  `"quant_perspective"`/`"day_trade_perspective"` to `fan_out`. Edges:
  `quant_perspective -> quant_analyst -> assessor`,
  `day_trade_perspective -> day_trade_analyst -> assessor`.
- TASK-6.8 (impl) `assess()`: attach all four fields — no change to the
  verdict/`effective_evidence`/`odds_adjustment` branch.
- TASK-6.9 (impl) `tests/conftest.py`'s `run_graph` fixture gains optional
  `bars=None, llm=None` passthrough kwargs.
- TASK-6.10 (verify) `capture_baseline.py --synthetic` unchanged output —
  its `LagMatrixContext` never sets `llm=`, and its `COLUMNS` don't extract
  the new fields either way.

**No `CachePolicy` on either analyst node** — unchanged reasoning from
Revision 1: D-46 already measured zero hits under `Send` fan-out for a
structurally similar node, and the model-version identity lives in
`runtime.context`, not the state a default cache key would hash. Cost is
controlled by `max_llm_candidates` and `max_tokens`, not a cache. (These
nodes are gather nodes now, not `Send` targets themselves, so `CachePolicy`
would not even attach the same way `graph_retriever`'s does — a further
reason not to reach for it here.)

## PHASE-7 — `runner.py`: bars frame, Batch client, one real capped run

**Completion criterion:** `uv run pytest` green; one real batch run against
the live Anthropic Batch API (gated on `ANTHROPIC_API_KEY`/
`LAGMATRIX_ANTHROPIC_API_KEY`, skipped otherwise, per Q-43's existing
pattern), with actual measured input/output tokens, wall-clock (including
poll time), and dollar cost recorded verbatim below.

- TASK-7.1 (impl) `runner.py`: read `data/bars.parquet` (already the
  ingest's nightly-refreshed artifact, D-97; no change to `MarketFeed`),
  trailing-windowed with the existing `TRAIL_PAD` convention, passed as
  `bars=`.
- TASK-7.2 (test) red/green: temp-parquet fixture proves the wiring;
  `FileNotFoundError` degrades to `bars=None`, following
  `_load_excluded_symbols`'s existing idiom.
- TASK-7.3 (impl) `runner.py`: `settings = load_settings()`; `ctx =
  LagMatrixContext(..., bars=bars, llm=build_analyst_client(settings,
  mode="batch"), max_llm_candidates=settings.max_llm_candidates)`.
- TASK-7.4 (test) red/green: no API key configured → `ctx.llm is None`, run
  completes exactly as PHASE-6 exercised (same "fresh clone, no
  credentials" precedent already used for excluded-symbols and
  ArangoDB/Qdrant).
- TASK-7.5 (verify) one real run, `max_llm_candidates` set low (5) for the
  first check. Record: total input/output tokens (Batch results carry
  per-request `usage`), total wall-clock including poll time, and realised
  cost against the ceiling below. If poll time is large enough to matter for
  how often the CronJob can run (it runs once nightly, so "large" here means
  multiple hours, not minutes) — note it; nothing in D-117's 76s measured
  runtime assumed an LLM step, and this is the first thing in the ingest
  path with genuinely unbounded external latency.

**Cost ceiling — Haiku, Batch API, no caching credit assumed (conservative).**
Haiku 4.5 batch rate: $0.50/$2.50 per MTok (50% off the $1/$5 standard rate —
[pecollective's cost-optimisation guide](https://pecollective.com/tools/claude-pricing-guide/)).
With `max_tokens=500` fixed and an estimated (not yet measured) ~900 input
tokens per call (system prompt + one candidate's evidence): per-call ceiling
`900/1e6*$0.50 + 500/1e6*$2.50 = $0.0017`. At `max_llm_candidates=20` (40
calls: 20 candidates x 2 perspectives), **the per-run ceiling is $0.068**. At
the team lead's cited "up to 65 movers" uncapped (130 calls), **$0.221** —
same caveat as Revision 1: "movers" and "candidates reaching the analyst
nodes" are not the same count (D-129 measured 13 followers for LULU alone),
which is why the cap binds candidates, not movers.

Documented (not yet assumed as stacking here): "the batch discount stacks
with prompt caching" ([pecollective](https://pecollective.com/tools/claude-pricing-guide/)).
Applying that would lower the ceiling further, but only for the
system-prompt portion of each call (~300 of ~1,400 total tokens in this
prompt shape — most of the token budget is per-candidate evidence, which is
deliberately never cached), and it is unconfirmed whether a large batch's
concurrent processing lets most requests actually land after a cache entry
is warm rather than racing to create one simultaneously. TASK-7.5's real run
is where this gets measured instead of assumed.

## PHASE-8 — `serve.py`: direct calls, graceful degradation, the SSE flag

**Completion criterion:** `uv run pytest tests/test_serve_llm.py -v` green
(mocking `build_analyst_client`, no network); manual verification that the
live page still serves with no API key configured.

- TASK-8.1 (impl) `serve.py`'s `stream()`: `ctx = LagMatrixContext(...,
  llm=build_analyst_client(load_settings(), mode="direct"),
  max_llm_candidates=load_settings().max_llm_candidates)`, alongside the
  existing `arango_topology=... if db is not None else None` line — same
  idiom, same function, no new pattern.
- TASK-8.2 (impl) `stream()`'s `emit("start", {...})` gains `"llm":
  ctx.llm is not None` alongside the existing `"graphrag": db is not None` —
  the mechanism the page already uses to tell a viewer "was this half of the
  system available for this run," extended to the new half rather than
  inventing a second one.
- TASK-8.3 (test) red/green: no API key configured →
  `build_analyst_client` returns `None`, the SSE stream completes exactly as
  before this plan, `"llm": false` in the start event — the page must not
  fail whole for want of a key, same as it doesn't fail whole for want of
  ArangoDB (D-117's idiom, applied here).

**Cost ceiling — Haiku, direct calls, with caching (realistic for this
path, since a live run's calls happen close together in time rather than in
one large concurrent batch, making a warm cache more likely to actually be
hit).** Standard Haiku rate $1/$5; cache write 1.25x input, cache read 0.1x
input on the ~300-token system-prompt portion only
([eesel AI](https://www.eesel.ai/blog/claude-opus-5-pricing) for the
multiplier structure). First call in a run: `300*1.25*$1/1e6 +
600*$1/1e6 + 500*$5/1e6 = $0.003375`. Every subsequent call: `300*0.1*$1/1e6
+ 600*$1/1e6 + 500*$5/1e6 = $0.002930`. At `max_llm_candidates=20` (40
calls, one first-call per perspective): **~$0.118/run ceiling**. Uncapped at
130 calls: **~$0.382/run ceiling**.

**Residual risk, named rather than solved:** the demo, unlike the once-
nightly batch, can be loaded repeatedly by anyone with cluster access, so its
*daily* cost scales with traffic in a way the batch path's doesn't.
`max_llm_candidates` bounds each individual run; nothing in this plan bounds
how many runs happen per day. Adding a traffic-level rate limit is a
separate decision this plan does not make — named under "Not built."

## PHASE-9 — Infra: credentials, egress (both paths), and the web pod's guard

Not a code phase for the graph; infra manifests only. **TDD-exempt:**
configuration/infrastructure wiring with no behavioural change to the
pipeline's logic.

- TASK-9.1 (impl) `infra/secrets-template/secrets.template.yaml`: add
  `anthropic-credentials` with key **`LAGMATRIX_ANTHROPIC_API_KEY`**
  (corrected from the first draft's `ANTHROPIC_API_KEY` — `Settings`'
  `env_prefix="LAGMATRIX_"` means that's the name `build_analyst_client`
  actually reads, matching the `lagmatrix-pg` secret's naming convention,
  not `alpaca-credentials`' bare-name one).
- TASK-9.2 (impl) `infra/k8s/22-lagmatrix-ingest-cron.yaml`: add
  `secretRef: {name: anthropic-credentials}` to the ingest container's
  `envFrom`. No `fsGroup` change — this is `envFrom`, not a volume mount;
  D-117's fsGroup fix applies only to the file-mounted `arango-pw` secret,
  and `alpaca-credentials`/`lagmatrix-pg` already prove `envFrom` secrets
  need no such fix.
- TASK-9.3 (verify, no change) `lagmatrix-ingest-egress` already permits
  443 to `0.0.0.0/0` except pod/service/LAN CIDRs — read directly, covers
  `api.anthropic.com` already.
- TASK-9.4 (impl) `infra/k8s/21-lagmatrix-web.yaml`: add the same
  `secretRef: {name: anthropic-credentials}` to the web container's
  `envFrom` (it currently has none — individual `env:` entries only; this
  is the first secret it consumes this way).
- TASK-9.5 (impl) `infra/k8s/23-lagmatrix-netpol.yaml`: widen
  `lagmatrix-web-egress` with a new `to:` block identical in shape to the
  ingest policy's — `ipBlock: {cidr: 0.0.0.0/0, except: [10.42.0.0/16,
  10.43.0.0/16, 192.168.10.0/24]}`, `ports: [{protocol: TCP, port: 443}]` —
  with a comment recording: this is a deliberate narrowing of D-121's
  default-deny, made because the owner wants live-demo LLM calls; the
  residual is that this pod now has general outbound 443, mitigated by
  `ClusterIP`-only/no-Ingress (nothing external reaches it inbound), its
  read-only `lagmatrix_ro` ArangoDB role, and the `except` list still
  blocking the trading namespace and the LAN.
- TASK-9.6 (impl) `infra/k8s/21-lagmatrix-web.yaml`: add an `await-netpol`
  init container to the Deployment, identical to the ingest's (same image,
  same `DENIED`/`ALLOWED` probe targets — the widened policy gives the web
  pod the same allow/except shape, so `arangodb:8529` remains a valid
  ALLOWED target and the LAN `:6443` remains a valid DENIED one). Comment
  in the manifest states the reasoning from this revision's opening section:
  `Recreate` strategy means this pod is fully recreated on every deploy
  (including this one), and it now holds both a live database credential and
  real internet egress — closer to the ingest's exposure profile during the
  pod-add window than "a Deployment that starts once and lives," which is
  why the guard travels with the pod, not just the workload class.

**Completion criterion:** manifests apply cleanly (`kubectl apply --dry-run=server`
against a real cluster context, per this repo's existing verification
habit); TASK-9.3/9.5's `ipBlock` shape is read back from the applied
resource, not assumed from the file, before this phase is called done.

## PHASE-10 — Did the quant perspective add anything: rule, Haiku, or Opus?

Extends Revision 1's PHASE-9 with a Haiku-vs-Opus arm the owner's model
decision makes newly affordable and newly relevant.

**Shared setup.** Reuse `scripts/experiment_lag_matrix.py`'s discovery/
validation split (D-93/D-100) on `data/bars-10y.parquet`, lag 0 (D-95). For
each pair, define **`retained`** (the binary ground truth every comparison
below predicts): `True` if the pair's validation-window `|corr|` stays at or
above its own discovery band's lower edge (e.g. a pair discovered at 0.35,
band 0.3-0.4, is `retained` if validation `|corr| >= 0.3`), else `False`.
This is a real, non-degenerate target — unlike D-95's own "sign holds"
statistic, which is already 96-99% true per band and would make "predict
sign holds" a nearly free win for any classifier, including a trivial
always-True one.

**Pre-registration A (full population, free, decided now):** within each of
D-95's bands, compare the mean validation `|corr|` between pairs whose
discovery-window split-half sign agrees vs. disagrees. **Decision rule:**
adds signal only if, in the two best-powered bands (0.3-0.4: 115,155 pairs;
0.4-0.5: 39,990 pairs), the "agrees" group beats "disagrees" by **>= 0.03
absolute correlation** (D-93's own bar). Smaller or reversed is a null.

**Sample for B and C, recomputed at Haiku/Opus batch rates.** Both B and C
call an LLM, so both draw from one **stratified random sample of 5,000
pairs** across the two best-powered bands — five times Revision 1's
Opus-only sample, affordable now because Haiku's batch rate is a fifth of
Opus's standard rate. Standard error on a proportion at n=5,000: `sqrt(0.25/5000)
≈ 0.71pp`; both decision rules below use a **1.4pp (2xSE) margin**.

**Pre-registration B (Haiku vs. the deterministic rule):** on the 5,000-pair
sample, the rule predicts `retained=True` when the pair's split-half sign
agrees (a single per-pair binary fact, not the aggregate percentage
`QuantPerspective.split_half_sign_agree_pct` reports at the candidate level —
same underlying computation, a different level of aggregation, stated so the
two are not confused). `quant_analyst`'s Haiku-backed
`replication_expectation` predicts the same target (`"high"` -> `True`,
`"low"` -> `False`; `"insufficient_data"` abstains, excluded from the
accuracy denominator and reported separately as an abstention rate).
**Decision rule:** Haiku adds signal only if its accuracy beats the rule's
accuracy, on the identical sample, by more than 1.4pp. Otherwise null.

**Pre-registration C (Opus vs. Haiku, same sample):** same accuracy
comparison, Opus's classification against Haiku's. **Decision rule:** Opus
is worth its ~5x marginal cost only if it beats Haiku's accuracy by more
than 1.4pp on this sample; a tie or smaller gap means Haiku (already the
shipped default per the owner's decision) stays, and this plan does not
switch it.

**Cost, computed from real batch rates for both models:** Haiku batch
$0.0017/call (PHASE-7's figure); Opus batch ($2.50/$12.50 per MTok, 50% off
$5/$25): `900/1e6*$2.50 + 500/1e6*$12.50 = $0.0085/call`. Testing both
models on 5,000 pairs: `5000 * ($0.0017 + $0.0085) = $51.0` — cheaper than
Revision 1's 1,000-pair Opus-only estimate ($17) once the batch discount is
correctly applied throughout, while buying more than 4x the statistical
power (0.71pp SE vs. 1.6pp at n=1,000).

- TASK-10.1 (impl, one-shot script, **TDD-exempt**, same exemption class as
  `experiment_lag_matrix.py`) `scripts/experiment_quant_perspective.py`,
  extending `experiment_lag_matrix.py`'s split/screen functions (DRY),
  submitting B/C's sample via `BatchAnalystClient` directly (non-interactive,
  cost-sensitive — the same reasoning that puts the nightly ingest on Batch
  applies here).
- TASK-10.2 (verify) run Pre-registration A in full; record the table.
- TASK-10.3 (verify, gated on `ANTHROPIC_API_KEY`) run B and C's 5,000-pair
  sample against both models; record accuracy, abstention rates, realised
  cost, and both verdicts against the pre-committed 1.4pp margins.
- TASK-10.4 (verify) log all three outcomes via `/spike-log open`,
  regardless of which way any of them fall.

**Completion criterion:** all three tables and verdicts recorded here and in
the spike log, including nulls.

## PHASE-11 — Day-trade: what "did this add anything" would require

Not a code phase. **TDD-exempt: documentation.** Unchanged in substance from
Revision 1's PHASE-10, renumbered.

- TASK-11.1: record, here and via `/spike-log open`, that scoring "did
  knowing the liquidity/gap history help a trade" still requires historical
  bid/ask spreads, actual fills, or borrow availability (Q-33, Q-22) — none
  of which exist in any file this repo has, and neither Haiku nor Opus
  changes that. A fluent, confident `reasoning` string is exactly the kind
  of output that invites being read as more validated than it is; this task
  exists to say plainly that it is not.
- TASK-11.2 (test): an internal-consistency sanity check only — over a small
  sample of real candidates, does `day_trade_analyst`'s `liquidity_tier`
  move monotonically with `median_dollar_vol`? This checks that the model
  reads the numbers correctly. **It is not, and must not be reported as,
  validation that the perspective helps anyone trade.**

**Completion criterion:** the spike-log entry exists; TASK-11.2 passes; no
claim beyond "the arithmetic and the model's reading of it are both checked"
is made anywhere for this perspective.

## Not built

- **A collapsed verdict fed back into `assess()`.** Unchanged.
- **`data/minute.parquet`-based intraday stats.** Unchanged — frozen one-off
  extract, no nightly refresh, independently confirmed by the team lead.
- **Extending `MarketFeed`/live Alpaca fetch for OHLCV.** Unchanged —
  `data/bars.parquet` already has what's needed.
- **`CachePolicy` on either analyst node.** Unchanged reasoning, restated in
  PHASE-6 given the corrected gather-node contract.
- **Batch API for the live demo, or direct calls for the nightly ingest.**
  Explicitly declined in both directions — see Success Criterion 3. Batch's
  latency profile is wrong for an interactive page; direct calls at 40x the
  per-call frequency of a capped batch would be paid unnecessarily for a
  path where nothing is waiting on the result synchronously either way.
- **A traffic-level rate limit on the live demo's LLM usage.** Named as a
  residual risk in PHASE-8, not solved here — `max_llm_candidates` bounds a
  single run, nothing bounds how many runs happen per day.
- **Retrying a truncated response with a larger `max_tokens` budget.**
  Unchanged — the fixed ceiling is the whole cost-control story here.
- **Fine-tuning, a prompt library beyond one fixed system prompt per
  perspective, or a model other than `Settings.model`.** Unchanged.
- **UI / `serve_index.html` surfacing of the four new fields**, beyond the
  single `"llm": bool` flag PHASE-8 adds to the existing SSE start event
  (which mirrors `"graphrag"`, already there). Anything richer is a separate
  decision.

## Halt conditions

- If PHASE-6's fan-out tests show any of the four new fields attributable to
  the wrong candidate — halt (D-89's exact failure class).
- If TASK-6.10 shows the synthetic golden file needs regeneration — halt and
  find out why before regenerating past it.
- If PHASE-10's script cannot reach `data/bars-10y.parquet` — halt rather
  than approximate on `data/bars.parquet`.
- If PHASE-7 or PHASE-10's real run shows `.with_structured_output()` (direct)
  or the forced-tool-use parse (batch) failing to produce a valid note at a
  non-trivial rate — halt and treat it as a design question, not something
  to catch-and-default past silently. A parse failure fabricated into an
  "ok" note would be a worse defect than the run halting.
- If TASK-9.6's `kubectl apply --dry-run=server` check, or a real rollout,
  shows the widened `lagmatrix-web-egress` policy does not actually cover
  what the `ChatAnthropic`/raw `anthropic` SDK reaches (e.g., a telemetry or
  update-check endpoint beyond `api.anthropic.com`) — halt and verify
  against a real image the same way D-101 verified fastembed's model-
  download path, rather than widening the `ipBlock` further on an
  assumption.
- If real data shows someone tempted to derive a spread proxy from `high -
  low` and hand it to the LLM as if it were real — halt. An LLM repeating a
  fabricated number back with confidence is not a mitigation.
- If PHASE-7's real batch run's poll-to-completion time turns out to run
  into hours rather than minutes — halt and reconsider whether Batch API is
  actually compatible with the `0 9 * * 2-6` nightly schedule's assumption
  that the whole ingest finishes well before the next open, rather than
  quietly widening the CronJob's implicit time budget.
