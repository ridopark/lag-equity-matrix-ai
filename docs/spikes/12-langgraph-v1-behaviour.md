# 12 — LangGraph 1.2.11 behaviour, probed rather than read

**Question:** What does `langgraph 1.2.11` actually do, as opposed to what its
documentation and our own research said it does?
**Date:** 2026-09-03 to 2026-09-05
**Status:** done — fourteen claims probed before the refactor, all re-probed
during it

## What we tried

Spike 11 read the docs and produced D-35. The `implementation-plan` agent then
refused to build on that reading and executed fourteen probes against the
installed package, recording them as V1-V14. Every phase re-probed the claims it
depended on rather than inheriting them, on the standing instruction that the
plan was a hypothesis. That instruction earned its keep: **eight claims failed on
contact.**

## What we measured

| # | Claim | Held? |
|---|---|---|
| V1 | A `Send` target sees **only** its Send arg — not merged state, not even keys set at invoke time | **yes** |
| V2 | A node on a static edge from N Send tasks runs **once**, on merged state | **yes** |
| V3 | A conditional edge out of a Send task runs **once per branch**, seeing that branch's writes | **yes** |
| V4 | Two Send branches writing the same plain channel raise `InvalidUpdateError` | **yes** |
| V5 | `add_conditional_edges(START, fan, [...])` may return `Send` objects; no dummy node needed | **yes** |
| V6 | `Runtime` is injected by **type annotation**, not parameter name | **yes** |
| V7 | `timeout=` on a sync node raises at compile time | **yes, but understated** — see below |
| V8 | `RetryPolicy` default `retry_on` returns False for `RuntimeError`/`ValueError`/`OSError` | **yes** |
| V9 | `error_handler` fires only for a solitary task in a superstep | **wrong mechanism, right conclusion** |
| V10 | A checkpointed run killed mid-fan-out resumes re-executing only the failed branch | **yes** |
| V11 | `interrupt()` surfaces as `out["__interrupt__"]`; `Command(resume=...)` resumes | **yes** |
| V12 | `CachePolicy` does not dedupe within a superstep; hits only on a later invocation | **yes** |
| V13 | A node with two static incoming edges from different supersteps runs **twice** | **yes** |
| V14 | `with_allowlist` silences the unregistered-type warning | **wrong, twice over** |

### The three that were wrong, and how they were caught

**V14 — wrong in two independent ways, caught by `phase3-red`.**
`saver.with_allowlist([...])` is a **no-op** at the installed default:
`allowed_msgpack_modules` defaults to the sentinel `True` and
`with_msgpack_allowlist` short-circuits `return self`. `clone is saver` returns
`True`. Worse, the message is emitted through stdlib `logging`, not
`warnings.warn` — so the plan's suggested `pytest.warns` assertion would have
been a **silent no-op**, passing identically whether or not the fix existed. The
working mechanism is `JsonPlusSerializer(allowed_msgpack_modules=[(module,
ClassName), ...])`; a bare `(module,)` tuple never matches. Recorded as D-44.

**V7 — true but understated, caught by `phase5-green` hitting a wall.**
The claim is correct: `timeout=` on a sync node raises at compile time. What the
plan drew from it — *"sync and async nodes mix freely, so only this one node
changes"* — is true of node *implementations* and false of *callers*. One
`async def` node anywhere makes sync `.invoke()` fail for the **entire graph**
(`TypeError: No synchronous function provided`), with no timeout involved.
Reproduced on a bare async node. Three cascades followed: `tests/test_news.py`,
`SqliteSaver` (which raises `NotImplementedError` on every async call, forcing
`AsyncSqliteSaver`), and `scripts/capture_trace.py` — the last discovered only
by running it, because it sits outside both the test suite and the criteria.

**V9 — right conclusion, wrong mechanism, caught by `phase5-red`.**
`error_handler` does **not** "fire only when alone". It fires under fan-out too —
it simply stops *suppressing* the exception once two or more tasks share a
superstep. With a single task it fires and suppresses. The conclusion the plan
drew (it cannot replace the news node's `try/except`) is correct; its stated
reason was not.

### The criteria failed more often than the claims

Five further failures were not in the V-table at all, but in the plan's
completion criteria:

| Phase | Criterion | Failure |
|---|---|---|
| 0 | "98 data rows" | Unsatisfiable against its own task — three candidates produce no assessment |
| 1 | `grep "def make_"` returns nothing | Contradicted TASK-1.5, which instructs keeping one |
| 2 | `grep "for c in candidates"` | Matched two pre-existing generator expressions |
| 2 | *(no `--news` clause)* | **Passed cleanly over a hard crash** |
| 5 | `SqliteSaver.from_conn_string(...)` with `serde=` | That signature has no `serde` parameter |

## Conclusion

The plan's **tasks** were built by execution and held up — V10, V12 and the
non-obvious call to run `Runtime` (item 2) *before* `Send` (item 1) were all
correct, the last because a Send target cannot see merged state, making the
context injection a prerequisite rather than a peer.

The plan's **criteria** were written by reading, and that is where every failure
was. Three were over-broad and cost a halt each. Two were too narrow and shipped
defects: PHASE-2's criterion exercised only `with_news=False`, passed every
clause, and shipped a `TypeError` crash; the identical blind spot later broke
`capture_trace.py`. **Over-broad criteria cost time; over-narrow criteria ship
bugs**, and the second failure mode is invisible precisely because everything
reports green.

Three habits caught these, in ascending order of what they cost:

1. **Execute the criterion, do not read it.** Cheap, and it found all three
   over-broad cases.
2. **Ask what else could satisfy this check.** Found V14's silent-no-op
   assertion and the news-injection test that a shape-only fix would have
   passed.
3. **Construct an environment where the wrong answer fails.** Unsetting
   `ALPACA_*` to prove the injection was real; mutating a verdict to prove
   `check_baseline.py` can fail; breaking D-27 in the producer to prove the
   protected tests still bite. This is the only one that catches a test
   observing the right outcome through the wrong mechanism, and nothing cheaper
   substitutes for it.
