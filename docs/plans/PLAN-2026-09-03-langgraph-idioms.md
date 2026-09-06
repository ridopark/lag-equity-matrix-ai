# PLAN-2026-09-03-langgraph-idioms

**Goal:** Refactor the LagMatrix graph onto LangGraph v1 idioms — `Send` fan-out,
`Runtime` dependency injection, a durable checkpointer, `CachePolicy`,
`RetryPolicy` + `timeout`, and `interrupt()` on a contradicted verdict — without
regressing the pipeline's output on the 98 real candidates.

**Decisions this depends on:** D-35 (the refactor itself), D-34 (no calibrated
confidence, no ArangoDB), D-27 (neighbourhoods from the wide universe), D-23
(`CandidateSource` is the only mode seam; no node branches on mode), D-19 (the
output must be able to say "contradicted"), D-36 (TDD is binding).

**Open questions that could invalidate it:** Q-08 (checkpointer choice) — this
plan *answers* it in PHASE-3 rather than assuming it; see the reasoning there.
Q-12 is untouched: the fusion maths does not change anywhere in this plan.

**Installed versions this plan was verified against:** `langgraph 1.2.11`,
`langgraph-checkpoint 4.2.0`, `langgraph-prebuilt 1.1.0`, Python 3.12. Every API
shape below was checked against `.venv/lib/python3.12/site-packages/langgraph/`
by execution, not by assumption. The probe results are recorded in
"Verified framework behaviour" and are what several phases are designed around.

---

## Baseline (measured 2026-09-03, before any change)

- `uv run ruff check` → **All checks passed!**
- `uv run pytest` → **7 passed**
- `data/fires.csv` → **98 candidates**, 83 distinct `(symbol, as_of)` pairs, 24
  distinct symbols, 47 distinct dates. All 13 duplicated pairs share the same
  `direction`.
- `data/bars.parquet` pivots to a `closes` frame of **158 sessions x 3210 symbols**.

---

## Success criteria

The plan is done when **all** of the following hold. Nothing may be substituted
or relaxed at execution time.

1. `uv run ruff check` passes and `uv run pytest` passes at every phase boundary,
   not only at the end.
2. The seven tests in `tests/test_graph_builder.py` are green throughout. Their
   **assertion lines are byte-identical to the baseline**; only graph
   construction and invocation lines may change, and only in PHASE-1.
3. `uv run python scripts/run_pipeline.py --limit 5` prints 5 assessments at
   every phase boundary.
4. `uv run python scripts/run_pipeline.py --limit 98` produces assessments whose
   `(symbol, as_of, direction, verdict, effective_evidence)` tuples match the
   pre-refactor baseline captured in TASK-0.1, for **all 98** candidates.
5. `src/lagmatrix/pipeline/runner.py` contains no `for c in candidates:` loop
   around `graph.invoke`.
6. Every node in `src/lagmatrix/graph/nodes/` is a module-level function
   importable and callable without constructing a graph. No `make_*` factory
   remains.
7. `grep -rn "add_node\|StateGraph\|compile(" src/lagmatrix/graph/builder.py`
   shows no `Command`, no `add_subgraph`/nested `StateGraph`, and no `defer=True`
   (D-35 declined all three).
8. A killed run resumes: the PHASE-3 test proves that after a mid-fan-out
   failure, re-invoking on the same `thread_id` re-executes only the failed
   branch.

---

## Verified framework behaviour (probe results this plan is built on)

These were established by running code against the installed `langgraph 1.2.11`.
Several of them contradict what a reasonable reading of the docs would suggest,
and three of them change the shape of the refactor. **Do not re-derive them from
first principles at execution time; if one turns out false, that is a halt
condition.**

| # | Behaviour | Consequence for this plan |
|---|---|---|
| V1 | A `Send` target node receives **only the Send `arg`**. It does not see the merged state at all — not even keys the caller set at invoke time. | `closes` and `signal_universe` cannot reach a fanned-out node through state. `Runtime` is the only mechanism that works. This is why PHASE-1 precedes PHASE-2. |
| V2 | A node reached by a **static** edge from N Send tasks runs **once**, on merged state. Per-branch isolation lasts exactly one node. | A multi-node per-candidate pipeline needs either a subgraph (declined by D-35) or candidate-keyed merged channels. This plan takes the latter. |
| V3 | A **conditional** edge out of a Send task runs **once per branch** and sees that branch's own writes over the input state. Returning `Send(...)` from it chains the fan-out. | The `graph_retriever -> (leader_state ‖ vector_retriever)` fan-out survives, per candidate. |
| V4 | Two Send branches writing the same **plain** channel raise `InvalidUpdateError: At key 'x': Can receive only one value per step`. | The identity channel carrying the branch's `Candidate` needs a `lambda _a, b: b` reducer. |
| V5 | `add_conditional_edges(START, fan, [...])` works and may return `Send` objects. | No dummy fan-out node is needed. |
| V6 | `Runtime` is injected by **type annotation** (`runtime: Runtime[Ctx]`), not by parameter name. An unannotated second parameter raises `TypeError`. | Node signatures must carry the annotation. |
| V7 | `timeout=` on a **sync** node raises at compile time: *"Node timeouts are only supported for async nodes because sync Python execution cannot be safely cancelled in-process."* | The news node must become `async def`, and the graph must be driven by `ainvoke`. Sync and async nodes mix freely under `ainvoke`. |
| V8 | `RetryPolicy.retry_on` defaults to `default_retry_on`, which returns **False** for `ValueError`, `TypeError`, `RuntimeError`, `OSError`, `LookupError` and friends, and **True** for everything else. `alpaca.common.exceptions.APIError` subclasses plain `Exception`, so it **is** retried. | A retry test that raises `RuntimeError` will observe zero retries and prove nothing. Tests must raise `APIError` or a bespoke `Exception` subclass. |
| V9 | `error_handler=` on a node fires **only when the failing task is the sole task in its superstep**. With two or more concurrent tasks the exception propagates and kills the run — sync and async alike. Reproduced at 2, 3 and 4 tasks. | `error_handler` **cannot** be used to preserve the news node's degrade-and-continue behaviour once `Send` fan-out exists. PHASE-5 is redesigned around this; see there. |
| V10 | With a checkpointer, a run killed mid-fan-out persists the successful branches' writes. Re-invoking with `ainvoke(None, config)` on the same `thread_id` re-executes **only the failed branch**. | This is what replaces the swallowed news error, and why PHASE-5 depends on PHASE-3. |
| V11 | `interrupt()` from a once-per-run node under fan-out works; the payload surfaces as `out["__interrupt__"]`, and `Command(resume=...)` resumes it. `Command(resume={interrupt_id: value})` also resolves several at once. | PHASE-6 is viable. Note that `Command` here is the *resume input to `invoke`* — not a node return value. D-35 declined the latter; this plan introduces no node that returns `Command`. |
| V12 | `CachePolicy` does **not** dedupe within a superstep: 4 Sends with 2 distinct args ran the node body 4 times. It hits only on a **later** invocation (2 of 4 on the second invoke). | See PHASE-4. This materially qualifies D-35's stated reason for item 4. |
| V13 | A node with two static incoming edges whose sources land in **different** supersteps runs **twice** — the first time with the later branch's channel still empty. | `vector_retriever` must stay behind `graph_retriever` (same superstep as `leader_state`), exactly as today. Moving it to the top-level fan-out silently runs `context_fusion` twice. |
| V14 | Pydantic models round-trip through the checkpoint serializer, but emit *"Deserializing unregistered type ... will be blocked in a future version"*. `saver.with_allowlist([...])` registers them. | PHASE-3 registers `lagmatrix.domain.models`. |

---

## Findings that qualify D-35 — must be logged, not silently absorbed

Two of D-35's stated justifications do not survive contact with the installed
version. Per `.claude/agents/implementation-plan.md` these are stated inline
rather than buried, and PHASE-7 records them.

- **F-1 — `CachePolicy` saves nothing within a run once `Send` lands.** Spike 11
  justified item 4 with *"with the per-candidate loop, repeats it for every
  candidate on the same date"*. PHASE-2 removes that loop, and V12 shows the
  cache does not dedupe concurrent tasks in one superstep. On the real data the
  first batched run therefore gets **0** cache hits, not 15. The cache retains
  real but smaller value across invocations in one process. The alternative that
  would actually save the 15 duplicate computations is deduplicating the fan-out
  by `(symbol, as_of)` — which would in turn make `CachePolicy` decorative.
  This plan keeps `CachePolicy` (D-35 decided it) with an **honest criterion**
  scoped to what it measurably does, and raises the tension for the log.
- **F-2 — `error_handler` is unusable under fan-out (V9).** The obvious framework
  replacement for the news node's hand-rolled `try/except` does not work in the
  configuration this refactor creates. PHASE-5 substitutes the checkpointer's
  resume path (V10), which is a better answer for a batch job, but it is a
  deliberate behaviour change and is called out as such.

---

## Phase order, and why item 2 precedes item 1

D-35 lists `Send` first and `Runtime` second. That ordering is **by value**, not
by execution. This plan runs `Runtime` first, for three reasons:

1. **`Runtime` is a prerequisite, not a peer.** V1 is decisive: a Send target
   sees only its Send arg. `closes` (a 158x3210 frame) cannot travel in the Send
   arg without being pickled into every branch's cache key and every checkpoint,
   and it cannot travel in merged state because the node never sees merged
   state. `Runtime.context` is the only injection route that survives PHASE-2.
   Doing `Send` first would mean building the fan-out on closures and then
   rewriting the same functions again.
2. **`Runtime` is topology-preserving and output-preserving.** It changes only
   how a node receives `closes` — not what it computes, not what edges exist,
   not what the state contains. It therefore lands under the existing seven
   tests as a pure regression net, at near-zero risk, and gives PHASE-2 a stable
   base.
3. **It installs the test seam once.** PHASE-1 adds a `run_graph(...)` helper to
   `tests/conftest.py`. After that, `tests/test_graph_builder.py` needs **no
   further edits at all** — PHASE-2's much larger change touches zero lines in
   it, which is what makes criterion 2 checkable rather than aspirational.

Everything after that follows D-35's order, with two dependencies added:
PHASE-5 and PHASE-6 both require PHASE-3, because the checkpointer is what makes
a failed news call recoverable (V10) and what `interrupt()` requires to exist at
all.

```
PHASE-0 (baseline capture, no code change)
   |
PHASE-1  Runtime + context_schema        [D-35 item 2]   <- prerequisite for 2
   |
PHASE-2  Send fan-out, keyed state       [D-35 item 1]   <- the significant one
   |
PHASE-3  Checkpointer + thread_id        [D-35 item 3]   <- prerequisite for 5, 6
   |
   +-- PHASE-4  CachePolicy              [D-35 item 4]
   +-- PHASE-5  RetryPolicy + timeout    [D-35 item 5]
   +-- PHASE-6  interrupt()              [D-35 item 6]
   |
PHASE-7  spike-log entries               (docs only)
```

PHASE-4, 5 and 6 are independent of one another and may ship in any order once
PHASE-3 is in.

---

## Requirements

- **REQ-1** Per-candidate fan-out is the graph's, not the runner's.
- **REQ-2** Node dependencies are passed explicitly; nodes are plain module-level
  functions.
- **REQ-3** A run is resumable and every assessment is replayable after the fact.
- **REQ-4** The expensive neighbourhood computation is cached.
- **REQ-5** The news call retries transient failures and is bounded in time.
- **REQ-6** A contradicted verdict pauses for a human before the run completes.
- **REQ-7** No cross-attribution: one candidate's neighbours, shocks or news may
  never appear in another candidate's assessment.
- **REQ-8** No subgraphs, no `Command` returned from a node, no deferred nodes.

---

## PHASE-0 — Capture the regression baseline

**Type:** one-shot script. No TDD (no behaviour change).
**Completion criterion:** `data/baseline-98.csv` exists, has 98 data rows, and
`git status` shows no modification to any file under `src/`.

- **TASK-0.1** Write `scripts/capture_baseline.py`: run `run(closes=..., limit=None,
  with_news=False)` over `data/bars.parquet` and write one CSV row per assessment
  with columns `symbol,as_of,direction,verdict,effective_evidence,n_supporting,
  n_contradicting`, sorted by `(as_of, symbol, direction)`. Round
  `effective_evidence` to 6 dp so float formatting cannot cause spurious diffs.
- **TASK-0.2** Run it. Commit `data/baseline-98.csv`.
- **TASK-0.3** Add `scripts/check_baseline.py` that regenerates the CSV and
  diffs it against the committed one, exiting non-zero on any difference. This
  is the executable form of success criterion 4 and is run at every subsequent
  phase boundary.

> Assumption stated inline: the pipeline is described as producing correct
> output on 95 of 98 candidates. This phase captures **all 98 rows including the
> 3 wrong ones**. The criterion is "unchanged", not "correct" — fixing the 3 is
> out of scope and would be a separate plan.

---

## PHASE-1 — `context_schema` + `Runtime` replaces the closure factories

**Implements:** D-35 item 2 / REQ-2. **Depends on:** PHASE-0.
**Type:** behaviour-preserving refactor. TDD applies — the new tests specify the
new callable surface (nodes as importable functions), which is itself the
behaviour change being introduced.

**Completion criterion:** `uv run pytest` green with the 7 original tests
unmodified in their assertions plus the new `tests/test_nodes.py`;
`uv run ruff check` clean; `uv run python scripts/check_baseline.py` exits 0;
`grep -rn "def make_" src/lagmatrix/graph/nodes/{graph_retriever,leader_state,context_fusion}.py`
returns nothing.

> **Amended during execution (2026-09-04).** As written this clause said
> `grep -rn "def make_" src/lagmatrix/graph/` returns nothing, which this plan's
> own tasks make unsatisfiable: TASK-1.5 instructs *keeping*
> `make_route_on_neighbourhood`, and TASK-1.1 defers `retrieve_news` to PHASE-5.
> The clause is scoped to the three modules this phase converts, and the
> requirement for the other two is **moved, not dropped** — to the criteria of
> PHASE-2 and PHASE-5, which own their removal. Recorded as D-39.

### Tests first

- **TASK-1.1 (test, red)** New `tests/test_nodes.py`. Each test imports a node
  directly and calls it with a hand-built runtime — **no graph is constructed**:

  ```python
  from langgraph.runtime import Runtime
  from lagmatrix.graph.context import LagMatrixContext
  from lagmatrix.graph.nodes.graph_retriever import retrieve_neighbourhood

  def test_retrieve_neighbourhood_is_callable_without_a_graph(closes):
      rt = Runtime(context=LagMatrixContext(closes=closes, signal_universe=set()))
      out = retrieve_neighbourhood({"candidates": [_candidate()]}, rt)
      assert out["lag_edges"]
  ```

  Cover `retrieve_neighbourhood`, `leader_state`, `fuse_evidence`. (`retrieve_news`
  is covered in PHASE-5 where it changes; `assess` and `publish` already take no
  dependencies.) Add one test asserting D-27 at the node level:
  `retrieve_neighbourhood` with `signal_universe={"LEAD1","LEAD2"}` returns no
  edge whose `leader` is in that set. Observe these fail — the modules currently
  export `make_*` factories, so the imports raise `ImportError`.

- **TASK-1.2 (test)** Add to `tests/conftest.py`:

  ```python
  @pytest.fixture
  def run_graph(closes):
      def _run(candidates, *, signal_universe=frozenset(), with_news=False):
          g = build_graph(with_news=with_news)
          return g.invoke(
              {"candidates": candidates},
              context=LagMatrixContext(closes=closes,
                                       signal_universe=set(signal_universe)),
          )
      return _run
  ```

  Rewrite the two setup lines of each of the seven tests in
  `tests/test_graph_builder.py` to use it. **Every `assert` line stays
  byte-identical.** Verify with
  `git diff -U0 tests/test_graph_builder.py | grep '^[-+].*assert'` returning
  nothing.

### Implementation

- **TASK-1.3 (impl)** New `src/lagmatrix/graph/context.py`:

  ```python
  @dataclass
  class LagMatrixContext:
      """Run-scoped dependencies. Passed via `invoke(..., context=...)`; nodes
      read it off `Runtime`, so nothing is hidden in a closure."""
      closes: pd.DataFrame
      signal_universe: set[str]
      trail: int = 60
      topk: int = 20
      move_win: int = 3
      sigma: float = 2.0
      cluster_rho: float = 0.7
      news_lookback_days: int = 5
      news_limit: int = 20
      news_client: object | None = None   # set in PHASE-5; None disables news
  ```

  The tunables currently live as default arguments on the factories
  (`trail=60`, `topk=20`, `move_win=3`, `sigma=2.0`) and as the module constant
  `CLUSTER_RHO`. Moving them here is the minimum needed to keep nodes
  parameterless; **do not add any tunable that does not already exist** (KISS,
  CLAUDE.md §2).

- **TASK-1.4 (impl)** Rewrite the three factory modules. Delete
  `make_retrieve_neighbourhood`, `make_leader_state`, `make_fuse_evidence`;
  export `retrieve_neighbourhood(state, runtime)`, `leader_state(state, runtime)`,
  `fuse_evidence(state, runtime)` with the `Runtime[LagMatrixContext]`
  annotation (V6). The `returns = closes.pct_change()` / `sessions = closes.index`
  lines move from factory scope to the top of each function body — that is a real
  per-call cost (`pct_change` over 158x3210), which PHASE-4's cache and the
  once-per-run nature of `fuse_evidence` both mitigate; the node bodies are
  otherwise copied verbatim.

- **TASK-1.5 (impl)** `builder.py`: `build_graph(*, with_news: bool = True)` —
  `closes` and `signal_universe` leave the signature.
  `StateGraph(LagMatrixState, context_schema=LagMatrixContext)`. Keep
  `make_route_on_neighbourhood` and the topology exactly as they are.

- **TASK-1.6 (impl)** `runner.py`: build the context once and pass
  `context=ctx` to each `graph.invoke`. **The per-candidate loop stays** — it is
  PHASE-2's job. Nothing about the runner's output changes.

- **TASK-1.7 (refactor)** `uv run ruff check`; remove the now-unused
  `CLUSTER_RHO` module constant if and only if PHASE-1's own changes orphaned it.

---

## PHASE-2 — `Send` fan-out and candidate-keyed state

**Implements:** D-35 item 1 / REQ-1, REQ-7, REQ-8. **Depends on:** PHASE-1.
**Type:** behaviour change. Full TDD.

**Completion criterion:** `uv run pytest` green (7 original + PHASE-1 tests +
new); `uv run ruff check` clean; `uv run python scripts/check_baseline.py`
exits 0 — **all 98 rows identical**; `grep -nE "^[[:space:]]*for c in candidates:"
src/lagmatrix/pipeline/runner.py` returns nothing; and
`grep -n "def make_route_on_neighbourhood" src/lagmatrix/graph/builder.py`
returns nothing (inherited from PHASE-1's amended clause — this phase owns that
factory's removal); and `uv run python scripts/run_pipeline.py --limit 2 --news`
exits 0.

> **Reopened during execution (2026-09-04).** The criterion above originally
> exercised only `with_news=False` — every test, `check_baseline.py` and both
> `run_pipeline.py` checks. Changing the `news` channel to a dict reducer while
> `vector_retriever` still returned a list therefore shipped a crash
> (`TypeError: 'list' object is not a mapping`) that the phase passed cleanly
> over. The `--news` clause is added so the parallel fan-out branch this phase
> creates is actually executed. Recorded as D-41.
>
> Note this is the opposite failure to PHASE-0/1/2's earlier amendments: those
> criteria were **too broad** and failed on literal reading while their intent
> held. This one was **too narrow** — it read as satisfied while the code was
> broken. Over-broad criteria cost a halt; over-narrow ones ship bugs.

> **Tightened during execution (2026-09-04).** The loop clause originally grepped
> the bare substring `for c in candidates`, which also matches two pre-existing
> generator expressions computing the bar-fetch window
> (`min(c.as_of for c in candidates)`, `max(...)`). Those are not the loop this
> phase removes and were present before it started. The pattern is anchored to
> the loop *statement* instead. This is a precision fix, not a relaxation — the
> requirement is unchanged and is still checked. Per D-39.

### Target shape

```
START --(conditional, Send x N)--> graph_retriever
                                        |
                        (conditional, per branch — V3)
                                        |
                     +------------------+------------------+
                     |                                     |
              Send(leader_state)                  Send(vector_retriever)
                     |                                     |
                     +--------------> context_fusion <-----+     (static, runs once — V2)
                                            |
                                        assessor
                                            |
                                        publisher --> END

no neighbourhood -> END  (per branch, D-27 short-circuit preserved)
```

`vector_retriever` stays **behind** `graph_retriever` rather than moving to the
top-level fan-out. V13 is the reason: sources landing in different supersteps
make `context_fusion` run twice, the first time with an empty channel. This also
preserves today's short-circuit — no neighbourhood means no news call.

### State

`graph/state.py` gains a key helper and switches the fan-in channels to
candidate-keyed dicts:

```python
def candidate_key(c: Candidate) -> str:
    """Identity of one branch. A string, not a tuple, so it survives the
    checkpoint serializer unambiguously."""
    return f"{c.symbol}|{c.as_of.isoformat()}"

def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}

def _last(_a, b):
    return b
```

| Channel | Before | After |
|---|---|---|
| `candidates` | `list[Candidate]` | unchanged (set at invoke; visible to the once-nodes, per V2) |
| `candidate` | — | `Annotated[Candidate, _last]` — the branch identity. The reducer is required by V4. |
| `lag_edges` | `list[LagEdge]` | `Annotated[dict[str, list[LagEdge]], _merge]` |
| `leader_shocks` | `list[Shock]` | `Annotated[dict[str, list[Shock]], _merge]` |
| `news` | `Annotated[list[NewsChunk], add]` | `Annotated[dict[str, list[NewsChunk]], _merge]` |
| `evidence` | `Annotated[list[Evidence], add]` | `Annotated[dict[str, list[Evidence]], _merge]` |
| `effective_evidence` | `float` | `dict[str, float]` |
| `assessments` | `list[Assessment]` | unchanged |
| `errors` | `Annotated[list[str], add]` | unchanged |

> `effective_evidence` being a single `float` is the concrete form of the
> cross-attribution bug the runner loop exists to avoid: `fuse_evidence`
> currently accumulates it across every candidate in state. Keying it is what
> makes a batched invocation correct.

The 13 duplicated `(symbol, as_of)` pairs collapse to one key. That is correct
and intended: their neighbourhoods, shocks and news are identical by
construction. `assessments` is still built by iterating `state["candidates"]`,
so **98 assessments still come out** — the key deduplicates *work*, not *output*.
TASK-2.2 tests exactly this.

### Tests first

- **TASK-2.1 (test, red)** `tests/test_fanout.py::test_two_candidates_do_not_cross_attribute`.
  Extend the `closes` fixture with a second candidate `CAND2` correlated to a
  *different* bloc. Invoke the graph once with `[CAND, CAND2]` and assert each
  assessment's `supporting + contradicting` symbols are disjoint from the other's
  neighbourhood. Under the current code this either raises or produces merged
  evidence — observe the failure before writing PHASE-2's implementation.
- **TASK-2.2 (test, red)** `test_duplicate_candidates_each_get_an_assessment`:
  invoke with the same `Candidate` twice; assert `len(out["assessments"]) == 2`
  and that both are equal in `verdict` and `effective_evidence`. This pins the
  key-dedupes-work-not-output property.
- **TASK-2.3 (test, red)** `test_batched_matches_per_candidate_loop`: for a
  three-candidate fixture, assert that one batched `invoke` yields the same
  `(symbol, verdict, effective_evidence)` set as three separate single-candidate
  invocations. This is the miniature of success criterion 4.
- **TASK-2.4 (test, red)** `test_candidate_without_neighbourhood_is_skipped`:
  a candidate whose symbol is absent from `closes` produces an `errors` entry
  and no assessment, while a valid candidate in the same batch still produces
  one. Pins the per-branch `END` short-circuit under fan-out.

### Implementation

- **TASK-2.5 (impl)** `state.py` per the table above.
- **TASK-2.6 (impl)** `builder.py`:
  - `fan_out_candidates(state) -> list[Send]` returning
    `[Send("graph_retriever", {"candidate": c}) for c in state["candidates"]]`,
    wired with `g.add_conditional_edges(START, fan_out_candidates, ["graph_retriever"])`
    (V5). Delete `g.add_edge(START, "graph_retriever")`.
  - Replace `make_route_on_neighbourhood` with a plain
    `route_on_neighbourhood(state)` that reads the **branch's own** candidate
    (V3) and returns `END` when that candidate has no edges, else
    `[Send("leader_state", arg), Send("vector_retriever", arg)]` where
    `arg = {"candidate": c, "lag_edges": {key: edges}}`. The `with_news` flag
    decides whether the second `Send` is emitted; keep the existing
    closure-over-`fan_out` shape only if `with_news` still needs it, otherwise
    pass it via `LagMatrixContext`.
  - Keep `g.add_edge("leader_state", "context_fusion")` and
    `g.add_edge("vector_retriever", "context_fusion")` as **static** edges —
    V2 makes `context_fusion` run once, V13 makes it run once *correctly*
    because both sources are in the same superstep.
- **TASK-2.7 (impl)** Nodes. `retrieve_neighbourhood`, `leader_state` and
  `retrieve_news` each lose their `for c in state["candidates"]` loop and operate
  on `state["candidate"]`, writing `{channel: {candidate_key(c): value}}` and
  echoing `{"candidate": c}` so the branch identity is available to the next
  conditional edge (V3/V4). `fuse_evidence` and `assess` keep their loops over
  `state["candidates"]` and index the keyed dicts. `publish` is untouched.
- **TASK-2.8 (impl)** `runner.py`: delete the loop and its comment; build the
  context, call `graph.invoke({"candidates": candidates}, context=ctx)` once,
  return `state.get("assessments", [])`.
- **TASK-2.9 (impl)** Delete the now-stale comment block in `state.py` that says
  *"One candidate per invocation: the lists below carry no candidate key, so a
  batch would cross-attribute one candidate's neighbours to another"* and
  replace it with a note on the keying. This comment is the documentation of the
  bug this phase fixes; leaving it would be actively misleading.
- **TASK-2.10 (refactor)** Confirm criterion 2: `git diff` on
  `tests/test_graph_builder.py` since PHASE-1 must be **empty**.

---

## PHASE-3 — Checkpointer with a `thread_id` per run

**Implements:** D-35 item 3 / REQ-3, and answers **Q-08**. **Depends on:** PHASE-2.
**Type:** behaviour change (resumability). TDD applies.

**Completion criterion:** `uv run pytest` green including the new resume test;
`uv run ruff check` clean; `check_baseline.py` exits 0;
`uv run python scripts/replay.py <thread_id>` prints the checkpoint history for
a completed run.

### The Q-08 decision this phase makes

**Use `SqliteSaver`, not `InMemorySaver`.** D-35 gave two motivations: a
98-candidate run that dies halfway must not restart from zero, and it must be
possible to ask *why did we say "contradicted" for NFLX on 2026-06-02* after the
fact. `InMemorySaver` delivers **neither** — it dies with the process, so the
crash case is unrecoverable in exactly the scenario that motivates it, and there
is nothing to replay tomorrow. Shipping `InMemorySaver` would make item 3
decorative, which is precisely the "feature-collecting" failure D-35 warns
against. Postgres is rejected under D-34 constraint 2's spirit: no server
process has earned its place at this scale.

- **TASK-3.1 (config, no TDD)** `uv add langgraph-checkpoint-sqlite`. Verified
  available: version **3.1.1**, requires `langgraph-checkpoint>=4.1.0,<5.0.0`,
  which the installed 4.2.0 satisfies. Add `data/checkpoints.sqlite` to
  `.gitignore`.

### Tests first

- **TASK-3.2 (test, red)** `tests/test_checkpointing.py::test_a_failed_run_resumes_only_the_failed_branch`.
  Compile the real graph with an `InMemorySaver` (a test does not need
  durability) and a monkeypatched `leader_state` that raises a bespoke
  `Exception` subclass on the second of three candidates and counts calls.
  Assert: the first `invoke` raises; `graph.get_state(config).values["lag_edges"]`
  already contains the two successful branches; a second `invoke(None, config)`
  with the failure disabled calls the node body **once**; the final state holds
  all three. V10 confirms this shape works.
- **TASK-3.3 (test, red)** `test_thread_id_isolates_runs`: two `invoke`s with
  different `thread_id`s over different candidate lists do not see each other's
  `lag_edges`.
- **TASK-3.4 (test, red)** `test_checkpointed_state_round_trips_pydantic_models`:
  after a run, `get_state(config).values["assessments"][0]` is an `Assessment`
  instance and `.candidate.symbol` reads back correctly, **and** no
  `"Deserializing unregistered type"` warning is emitted (assert with
  `pytest.warns`/`recwarn` or `filterwarnings("error")`). V14 says this warning
  appears until the allowlist is set, so this test drives TASK-3.6.


  > **Corrected during execution (2026-09-04). V14 was wrong twice, both found
  > by probing rather than reading.**
  > (1) `saver.with_allowlist([...])` is a **no-op** at the installed default:
  > `allowed_msgpack_modules` defaults to the sentinel `True` and
  > `with_msgpack_allowlist` short-circuits `return self` — `clone is saver`
  > returns `True`.
  > (2) The message is emitted through **stdlib `logging`**
  > (`logger.warning` in `serde/jsonplus.py`), not `warnings.warn`, so the
  > suggested `pytest.warns` / `recwarn` / `filterwarnings("error")` assertion
  > would be a **silent no-op** — passing identically whether or not the fix
  > exists.
  >
  > Assert with **`caplog`** on `langgraph.checkpoint.serde.jsonplus`, after
  > clearing the process-global `jp._warned_unregistered_types` set (it dedups
  > per `(module, name)` per process, so test order alone could make it pass for
  > the wrong reason). Entries are `(module, ClassName)` pairs; a bare
  > `(module,)` tuple never matches. See D-44.

### Implementation

- **TASK-3.5 (impl)** `build_graph(*, with_news=True, checkpointer=None)` →
  `g.compile(checkpointer=checkpointer)`. Default `None` keeps every existing
  test compiling bare, so the seven originals stay untouched.
- **TASK-3.6 (impl)** `runner.py`: open
  `SqliteSaver.from_conn_string("data/checkpoints.sqlite")` as a context manager,
  construct it with `serde=JsonPlusSerializer(allowed_msgpack_modules=[...])`
  naming every domain model as a `(module, ClassName)` pair — **not**
  `.with_allowlist()`, which is a no-op (D-44), and **not**
  `LANGGRAPH_STRICT_MSGPACK`, which would *block* anything unlisted and trade a
  deprecation warning for a runtime crash on a forgotten model — and pass
  `config={"configurable": {"thread_id": thread_id}}`. `run()` gains
  `thread_id: str | None = None`; when `None` it defaults to
  `f"{as_of or 'all'}-{uuid4().hex[:8]}"` and the chosen value is returned to the
  caller — a run whose id you cannot recover is not replayable.
- **TASK-3.7 (impl)** `scripts/replay.py <thread_id>` (one-shot script, no TDD):
  iterate `graph.get_state_history(config)` and print each checkpoint's step,
  the nodes that ran, and the assessments present. This is D-35's "time travel,
  cheaply" — it is a script, not a graph feature, which is the point.
- **TASK-3.8 (impl)** `scripts/run_pipeline.py`: add `--thread-id` and print the
  thread id used, so the smoke check hands you the handle for `replay.py`.

---

## PHASE-4 — `CachePolicy` on the neighbourhood node

**Implements:** D-35 item 4 / REQ-4. **Depends on:** PHASE-3. Independent of 5, 6.
**Type:** behaviour change (call count). TDD applies.

**Read F-1 before starting.** V12 means the criterion below is deliberately
narrower than spike 11's prediction, and the phase is not complete until the
spike-log entry in PHASE-7 records why.

**Completion criterion:** the TASK-4.1 test passes — a **second** `invoke` of the
same compiled graph over the same candidates executes the `retrieve_neighbourhood`
body **zero** times; the first `invoke` executes it once per candidate;
`check_baseline.py` exits 0; `ruff` and `pytest` clean.

- **TASK-4.1 (test, red)** `tests/test_caching.py::test_neighbourhood_is_recomputed_only_once_across_invocations`.
  Wrap `retrieve_neighbourhood` in a counting spy. Compile with
  `cache=InMemoryCache()`. Invoke twice with the same three candidates on two
  different `thread_id`s. Assert the body ran 3 times then 0 times.
- **TASK-4.2 (test, red)** `test_cache_does_not_dedupe_within_one_invocation`.
  Invoke once with the same candidate listed twice; assert the body ran
  **twice**. This is not a wish — it pins V12 as observed behaviour so a future
  reader is not misled by the presence of a cache, and it is the executable form
  of F-1.
- **TASK-4.3 (impl)** `builder.py`:
  `g.add_node("graph_retriever", retrieve_neighbourhood, cache_policy=CachePolicy(ttl=3600))`
  and `g.compile(..., cache=cache)` with `cache: BaseCache | None = None` added
  to `build_graph`. `runner.py` passes a fresh `InMemoryCache()`.
- **TASK-4.4 (impl)** Use the **default** `key_func`. It pickles the Send arg,
  i.e. the `Candidate`; on the real data all 13 duplicate pairs share
  `direction`, so a custom `(symbol, as_of)` key would produce the identical
  hit count. A custom key here would be unrequested configurability (CLAUDE.md
  §2). **Record the hazard in a comment:** the cache key does *not* include
  `closes`, so a graph reused across two different bar frames would serve stale
  neighbourhoods. `runner.py` creates the cache in the same scope as the frame,
  which makes cache lifetime equal to frame lifetime; the `ttl=3600` is the
  belt-and-braces.

---

## PHASE-5 — `RetryPolicy` + `timeout` on the news node

**Implements:** D-35 item 5 / REQ-5. **Depends on:** PHASE-3 (essentially, not
just for ordering). Independent of 4, 6.
**Type:** behaviour change. Full TDD.

**Read F-2 and V7, V8, V9 before starting.** This phase deviates from D-35's
literal wording in one respect and the deviation is deliberate.

### What changes and why

D-35 says *"`RetryPolicy` + `timeout` on `vector_retriever`, removing its
hand-rolled try/except"*. Two installed-version facts reshape this:

- **V7:** `timeout=` is rejected at compile time on a sync node. `retrieve_news`
  must become `async def`, and the graph must be driven by `ainvoke`. Sync and
  async nodes mix freely under `ainvoke`, so **only this one node changes**;
  `runner.run()` becomes `async def` with a thin sync wrapper for
  `scripts/run_pipeline.py`. `NewsClient.get_news` is sync and the client
  exposes no timeout parameter, so the node awaits
  `asyncio.to_thread(client.get_news, req)`. State honestly in the docstring:
  `NodeTimeoutError` caps the graph's wall clock, it does not kill the
  underlying socket.
- **V9:** `error_handler=` — the obvious framework replacement for the
  `try/except` — fires only when the failing task is alone in its superstep.
  Under PHASE-2's fan-out it never fires, and the run dies. **So the `try/except`
  cannot be replaced by `error_handler`.**

The resolution is to remove the `try/except` anyway and let the failure
propagate, because PHASE-3 changed what a propagated failure costs. Today an
Alpaca outage silently degrades the evidence and the run reports assessments
built on missing news. After PHASE-3, the run halts and `ainvoke(None, config)`
resumes it, re-executing **only** the failed branch (V10). For a daily batch job
whose product is a defensible judgment (D-19, D-34), halting is the correct
behaviour and silent degradation is the bug. This is a **deliberate behaviour
change** and TASK-5.3 tests it explicitly.

Blast radius on the success criteria: none. `scripts/run_pipeline.py` defaults
to `--news` **off**, so criteria 3 and 4 are computed with no news call at all.

**Completion criterion:** `uv run pytest` green including the three new tests;
`uv run ruff check` clean; `check_baseline.py` exits 0;
`uv run python scripts/run_pipeline.py --limit 5` unchanged;
`grep -n "except Exception" src/lagmatrix/graph/nodes/vector_retriever.py`
returns nothing; and the compiled graph's `vector_retriever` node carries both a
`RetryPolicy` and a non-`None` timeout.

> **Clause added during execution (2026-09-05).** TASK-5.2's test necessarily
> uses its own one-node graph with a short timeout — asserting through
> `build_graph`'s production `timeout=30.0` would require a 30-second sleep. That
> is the right seam for the test, but it leaves the *production wiring* asserted
> by nothing: a green phase that made `retrieve_news` async and forgot
> `timeout=` in `builder.py` would pass every existing check. The structural
> clause closes that gap without a slow test. Per D-41 — a criterion must
> exercise what the phase creates.
Also: `grep -n "def make_retrieve_news" src/lagmatrix/graph/nodes/vector_retriever.py` returns nothing (inherited from PHASE-1's amended clause — this phase owns that factory's removal).

- **TASK-5.1 (test, red)** `tests/test_news_node.py::test_transient_failure_is_retried`.
  A fake news client raising `alpaca.common.exceptions.APIError` on its first
  two calls and succeeding on the third, injected via
  `LagMatrixContext.news_client`. Assert the run succeeds and the client was
  called 3 times. **V8 is the trap here:** a fake raising `RuntimeError` would
  observe zero retries because `default_retry_on` excludes it. The test must
  raise `APIError` (or a bespoke `Exception` subclass) and a comment must say
  why.
- **TASK-5.2 (test, red)** `test_timeout_fires_on_a_slow_news_call`: a fake
  client that sleeps past `timeout`; assert `NodeTimeoutError`.
- **TASK-5.3 (test, red)** `test_exhausted_news_failure_halts_and_resumes`:
  with a checkpointer and three candidates, a client failing permanently for one
  symbol; assert the first `ainvoke` raises, and that after the fake is fixed
  `ainvoke(None, config)` completes with all three candidates' news present and
  the other two branches not re-executed. This is the test that pins the
  behaviour change.
- **TASK-5.4 (impl)** `vector_retriever.py`: `async def retrieve_news(state,
  runtime)`, one candidate per call, client from `runtime.context.news_client`
  (constructed in `runner.py` from `Settings`, not from `os.environ` at import —
  the current module-level `os.environ["ALPACA_API_KEY"]` inside the factory
  means importing the module in a test without credentials is already awkward).
  Delete the `try/except`.
- **TASK-5.5 (impl)** `builder.py`:
  ```python
  g.add_node("vector_retriever", retrieve_news,
             retry_policy=RetryPolicy(max_attempts=3),
             timeout=30.0)
  ```
- **TASK-5.6 (impl)** `runner.py`: `async def run(...)`; add
  `def run_sync(*a, **kw): return asyncio.run(run(*a, **kw))` and point
  `scripts/run_pipeline.py` at it. Keep the change to the script to the import
  and call site.

---

## PHASE-6 — `interrupt()` on a contradicted verdict

**Implements:** D-35 item 6 / REQ-6. **Depends on:** PHASE-3. Independent of 4, 5.
**Type:** behaviour change. Full TDD.

**Completion criterion:** `uv run pytest` green including the new tests;
`ruff` clean; `check_baseline.py` exits 0 (the default path must not interrupt);
`uv run python scripts/run_pipeline.py --limit 5` still prints 5 assessments.

### Design note: a separate `review` node, not `interrupt()` inside `assess`

`interrupt()` raises, so a node that interrupts produces **no writes**. Calling
it inside `assess` would discard the assessments and leave the interrupt payload
with nothing to show the human. Instead a new `review` node sits between
`assessor` and `publisher`: assessments are already committed to state, `review`
reads them, and the interrupt payload carries the contradicted ones. On resume
LangGraph re-executes the node from the top (documented in `interrupt`'s
docstring), which is safe because `review` is pure — a property `assess` does
**not** have, since it stamps `ts=datetime.now(UTC)`.

`Command` appears here **only as the resume input to `ainvoke`** (V11). No node
returns a `Command`; REQ-8 and D-35's decline are intact. Say so in the
builder's module docstring so a reviewer does not mistake it for the declined
primitive.

- **TASK-6.1 (test, red)** `tests/test_review.py::test_contradicted_verdict_interrupts`.
  Fixture producing one contradicted candidate; run with
  `halt_on_contradicted=True`; assert `out["__interrupt__"]` exists and its
  `value` names the contradicted symbol, and that `out["assessments"]` is
  already populated.
- **TASK-6.2 (test, red)** `test_resume_after_approval_completes_the_run`:
  `ainvoke(Command(resume=True), config)` returns with `approved is True` and
  the same assessments.
- **TASK-6.3 (test, red)** `test_no_contradicted_verdict_does_not_interrupt`:
  a corroborated-only batch runs to completion with no `__interrupt__` key even
  when `halt_on_contradicted=True`.
- **TASK-6.4 (test, red)** `test_default_path_never_interrupts`: with
  `halt_on_contradicted=False` (the default) a contradicted batch completes.
  This is what protects success criteria 3 and 4.
- **TASK-6.5 (impl)** `src/lagmatrix/graph/nodes/review.py`:
  ```python
  def review(state: LagMatrixState, runtime: Runtime[LagMatrixContext]) -> dict:
      if not runtime.context.halt_on_contradicted:
          return {"approved": True}
      bad = [a for a in state.get("assessments", []) if a.verdict == "contradicted"]
      if not bad:
          return {"approved": True}
      answer = interrupt({
          "reason": "contradicted verdicts require confirmation (D-19)",
          "contradicted": [
              {"symbol": a.candidate.symbol, "as_of": str(a.candidate.as_of),
               "rationale": a.rationale} for a in bad
          ],
      })
      return {"approved": bool(answer)}
  ```
  Add `halt_on_contradicted: bool = False` to `LagMatrixContext` and
  `approved: bool` to `LagMatrixState`.
- **TASK-6.6 (impl)** `builder.py`: `assessor -> review -> publisher`.
- **TASK-6.7 (impl)** `runner.py`: `run(..., halt_on_contradicted: bool = False)`;
  when the result carries `__interrupt__`, return the assessments **and** the
  interrupt payload rather than swallowing it. `scripts/run_pipeline.py` gains
  `--review` (default off) which prints the contradicted list and the
  `thread_id` needed to resume.

---

## PHASE-7 — Record what the refactor learned

**Type:** documentation. No TDD.
**Completion criterion:** `docs/spikes/overall.md` contains the entries below and
`uv run python .claude/skills/spike-log/audit.py` (if it validates shape) passes.

- **TASK-7.1** Append a decision entry recording **F-1**: `CachePolicy` on
  `graph_retriever` does not dedupe within a superstep, so once `Send` removed
  the runner loop the cache saves nothing on a first batched run. State the
  alternative that lost (deduplicating the fan-out by `(symbol, as_of)`, which
  would have made `CachePolicy` decorative) and that D-35 item 4 was kept with a
  narrowed criterion.
- **TASK-7.2** Append a decision entry recording **F-2**: `error_handler` fires
  only for a solitary task in a superstep in `langgraph 1.2.11`, so the news
  node's degrade-and-continue behaviour was replaced by halt-and-resume on the
  checkpointer rather than by the framework's error handler. Name the
  alternative that lost (keeping the hand-rolled `try/except`, which would have
  prevented `RetryPolicy` from ever seeing the exception).
- **TASK-7.3** Append a decision entry closing **Q-08**: `SqliteSaver` at
  `data/checkpoints.sqlite`, with the reasoning that `InMemorySaver` satisfies
  neither motivation D-35 gave. Move Q-08 to struck-through in the Open
  Questions table.
- **TASK-7.4** Update D-35's **Outcome** field from `pending` to what was
  actually observed, including that `timeout` forced the news node async (V7)
  and that the plan reordered items 2 and 1.
- **TASK-7.5** Update D-01's Outcome — it still reads *"pending —
  `build_graph()` is still a stub"*, which has been false for some time and is
  flatly wrong after this plan.
- **TASK-7.6** Write `docs/spikes/12-langgraph-v1-behaviour.md` in the
  Question → What we tried → What we measured → Conclusion shape, holding the
  V1-V14 table. The probes are the evidence for two decision entries; without
  the spike file they are unreproducible folklore.

---

## Halt conditions

Stop and report rather than working around:

- **Any of V1-V14 fails to reproduce.** Several phases are shaped by them; a
  false one invalidates the phase, not just a task. Re-run the probe, record
  what actually happens, and stop.
- **`check_baseline.py` reports a diff on any of the 98 rows.** The refactor is
  behaviour-preserving by definition; a changed verdict means a bug, not an
  improvement to be rationalised. Exception: PHASE-6 with `--review` on is
  *expected* to halt rather than complete — run the baseline check with the
  default flags.
- **Any assertion line in `tests/test_graph_builder.py` changes after PHASE-1.**
  Those seven tests encode D-27 and D-34; editing an assertion to make a phase
  pass is the failure mode this criterion exists to catch.
- **A phase needs a subgraph, a node returning `Command`, or `defer=True`** to
  work. D-35 declined all three with reasons. If PHASE-2 cannot be made to work
  without one, that is a finding that reopens D-35, not a licence to add one.
- **`uv add langgraph-checkpoint-sqlite` fails to resolve.** Do not silently
  substitute `InMemorySaver` — that would ship a decorative item 3. Report and
  let Q-08 be re-decided.
- **PHASE-5's async conversion reaches beyond `vector_retriever`, `runner.run`
  and the `run_pipeline.py` call site.** Async creep through the node layer is
  out of scope; the whole point of the mixed sync/async support is that it does
  not need to spread.
- **Three consecutive failed attempts at one phase.**
