# 11 — LangGraph idioms: what the current build gets wrong

**Question:** Is the pipeline using LangGraph the way LangGraph is meant to be
used — checkpoints, fan-out/fan-in — and which other primitives earn their place
in a portfolio piece?
**Date:** 2026-09-03
**Status:** done (docs research against the installed `langgraph 1.2.11`)

## What we tried

Read the v1 graph API against the implementation written this session. Judged
each primitive twice: does it fit *this* problem, and does using it demonstrate
judgment rather than feature-collecting.

## What we measured

### Five things the current build gets wrong

**1. Per-candidate looping instead of the `Send` API — the significant one.**

The runner loops in Python and invokes the graph once per candidate, because
batching cross-attributed one candidate's neighbours to another. That is a
workaround for a problem LangGraph solves natively. `Send` fans out from a
conditional edge with a *per-branch state slice*:

```python
from langgraph.types import Send
def fan_out(state): return [Send("assess_one", {"candidate": c}) for c in state["candidates"]]
```

Each branch gets its own isolated state, reducers merge at fan-in. That is the
map-reduce pattern the problem actually is, and it makes the parallelism the
graph's rather than the loop's.

**2. Closures for dependency injection instead of `Runtime`.**

Nodes are built by factories (`make_retrieve_neighbourhood(closes, ...)`). v1
supplies run-scoped dependencies properly:

```python
@dataclass
class Ctx: closes: pd.DataFrame; signal_universe: set[str]

def retrieve(state: LagMatrixState, runtime: Runtime[Ctx]) -> dict:
    closes = runtime.context.closes

graph = StateGraph(LagMatrixState, context_schema=Ctx)
graph.invoke(inp, context=Ctx(closes=..., signal_universe=...))
```

Nodes become plain module-level functions — importable, testable without
constructing a graph, and the wiring stops hiding data in closures.

**3. No checkpointer at all.**

`compile()` is called bare, so nothing persists and `thread_id` is unused. For
this project that is a missed capability rather than a style point: a run over
98 candidates that dies halfway restarts from zero, and there is no way to ask
*why did we say "contradicted" for NFLX on 2026-06-02* after the fact.
Checkpointing gives a per-assessment audit trail for free — which matters for a
system whose entire product is a defensible judgment.

**4. No retry or timeout on the network node.**

`vector_retriever` calls Alpaca inside a bare `try/except` that swallows the
error into `errors`. The framework already does this better:
`add_node(..., retry_policy=RetryPolicy(max_attempts=3), timeout=30.0)`.

**5. No caching on the expensive node.**

`graph_retriever` computes a correlation of one candidate against ~3,200 columns
over 60 sessions, and — with the per-candidate loop — repeats it for every
candidate on the same date. `cache_policy=CachePolicy(ttl=...)` with
`compile(cache=InMemoryCache())` removes that duplication.

### What the build already gets right

- **Reducers on the fan-in keys.** `Annotated[list[NewsChunk], add]` and the same
  for `evidence`/`errors` — the correct mechanism for merging parallel branches.
- **A genuine parallel fan-out.** `leader_state` and `vector_retriever` run
  concurrently from one conditional edge and rejoin at `context_fusion`. That is
  a real fan-out/fan-in, not two sequential nodes.
- **Conditional routing** that short-circuits to `END` when there is no
  neighbourhood.
- **Nodes returning partial state**, not mutating it.

### Other primitives, judged rather than listed

| Primitive | Verdict for this project |
|---|---|
| **`interrupt()` HITL** | **Use.** A layer whose job is to say "contradicted" is exactly where a human should confirm before acting. Pausing on a contradicted verdict is the honest use of the feature, not a bolted-on demo. |
| **Time travel** (`checkpoint_id` replay) | **Use, cheaply.** Comes free with the checkpointer; a script that replays a past assessment is the audit story made concrete. |
| **Streaming** (`stream_mode`) | Worth it only when there is a UI. Not yet. |
| **`Command`** (update + goto) | Marginal here — the routing is already a clean conditional edge. Would add indirection without removing any. |
| **Subgraphs** | **Skip.** The graph is seven nodes deep. Nesting would be architecture theatre. |
| **Deferred nodes** | **Skip.** The reducer fan-in already handles the join. |

The last three matter as much as the first four. A portfolio reviewer who knows
LangGraph will notice a project that used subgraphs on a seven-node graph, and
will read it as feature-collecting rather than design. Declining primitives that
do not fit is the part that reads as judgment.

## Conclusion

The fan-out/fan-in is genuinely correct; checkpointing is absent entirely; and
the per-candidate loop is the one real design error — it hand-rolls in Python
the thing `Send` exists to do.

Priority order, highest value first:

1. **`Send`** — replaces the runner loop, fixes the attribution issue at the
   framework level rather than around it.
2. **`context_schema` + `Runtime`** — removes the closure factories, makes nodes
   plain functions.
3. **Checkpointer + `thread_id`** — resumability and an audit trail.
4. **`CachePolicy` on `graph_retriever`** — removes repeated correlation work.
5. **`RetryPolicy` + `timeout` on `vector_retriever`** — replaces the hand-rolled
   try/except.
6. **`interrupt()` on a contradicted verdict** — the one HITL use that fits.
