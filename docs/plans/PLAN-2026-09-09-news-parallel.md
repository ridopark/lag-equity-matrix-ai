# PLAN-2026-09-09-news-parallel

**Goal:** Stop the live diagram implying `vector_retriever` depends on
`graph_retriever`'s output — it doesn't, since D-83 — without breaking
`context_fusion`'s exactly-once join, which the literal "Send it from `START`"
design does break (proven below, not assumed).

**Decisions this depends on:** D-16, D-27, D-35 (declines `defer=True`,
subgraphs, `Command` — this is why the recommended design below is not the
literal ask), D-81, D-83, D-85, REQ-1

**Open questions that could invalidate it:** none of Q-42/Q-43 bear on this
change (both explicitly out of scope, untouched). This plan registers a new
one, Q-44, on whether true `START`-level parallelism is worth a larger
redesign — see "Rejected design" below. If Q-44 is answered "yes" later, this
plan does not need to be undone; it is a strict improvement either way.

## The central finding, checked empirically rather than assumed

The task as given ("rewire `vector_retriever` to fan out from `START` in
parallel with `graph_retriever`") assumes LangGraph will run `context_fusion`
exactly once, seeing both branches' writes, regardless of which superstep each
branch completes in. **That assumption is false for the installed
`langgraph==1.2.11`.** `context_fusion` has two static incoming edges today
(`leader_state -> context_fusion`, `vector_retriever -> context_fusion`); it
runs exactly once *only because* both sources currently complete in the same
Pregel superstep (both are Sent by the one `route_on_neighbourhood` call).
A node with two static in-edges from sources that complete in *different*
supersteps runs once per superstep in which either source fires — i.e. twice,
the first time seeing only whichever source finished first.

Verified directly against the installed version, mirroring this graph's exact
join shape (`A`/`V` both Sent from `START`, `A` conditionally forwards to `L`
one superstep later, `L` and `V` both edge into join node `J`):

```python
# START -(Send)-> {A, V}; A -(conditional Send)-> L; L, V -> J
out = compiled.invoke({})
# j_calls: [
#   "j sees a_out=['a:X'] v_out=['v:X']",                 # J's 1st firing: L hasn't run yet
#   "j sees a_out=['a:X', 'l:X'] v_out=['v:X']",           # J's 2nd firing
# ]
# num J executions: 2
```

Mapped onto this codebase: if `vector_retriever` fans out from `START` (superstep
1, same wave as `graph_retriever`) while `leader_state` still fans out from
`route_on_neighbourhood` (superstep 2, one wave later because it genuinely needs
`graph_retriever`'s neighbourhood — the dependency the task brief itself confirmed
is real), `context_fusion` fires **twice** for any candidate that has both a
neighbourhood and news: once after `vector_retriever` completes (superstep 2,
`leader_shocks` not yet written — every `movers` list is empty, so `origin_leader`
candidates get a spurious `"no shock for candidate's own move"` error appended to
the `errors` channel even when the shock exists and simply hasn't arrived yet),
and again after `leader_state` completes (superstep 3, full data). Because
`evidence` and `errors` are `Annotated[..., add]` (concatenating, not
overwriting) reducers, the two firings' outputs are *summed*, not replaced —
`out["evidence"]` would contain a corrupted mix of a partial pass and a full
pass. `evidence_by_key`/`effective_evidence_by_key` (dict `_merge`, last-write
wins per key) would self-heal by the second, complete firing, but the flat
`evidence`/`effective_evidence`/`errors` channels — which several existing
tests read directly (`test_neighbourhood_excludes_the_signal_universe`,
`test_effective_evidence_never_exceeds_raw_count`, the reentry tests in
`test_market_scan.py`) — would not.

**Conclusion: the literal design is not safe to implement as specified.**

### Rejected alternatives, and why

- **Send `vector_retriever` from `START` as asked.** Breaks `context_fusion`'s
  single-execution guarantee, per the probe above. Rejected.
- **Make `context_fusion` idempotent under repeated partial firings** (e.g.
  re-derive `evidence`/`effective_evidence` from scratch each time with a
  replacing rather than additive reducer, or track per-key completion and only
  emit once both `leader_shocks` and `news` are present for every candidate).
  Possible in principle, but it rewrites the well-tested, delicate
  independence-weighting logic (Q-12's cluster discount) and its reducer
  contract for a diagram-accuracy problem. Disproportionate. Rejected for this
  plan; left as the substance of Q-44 if the team wants true parallelism badly
  enough to fund it.
- **Add `defer=True` to `context_fusion`** (LangGraph's actual primitive for
  "wait until every other pending task in the run has finished"). D-35
  explicitly declined this, and it would also be broader than needed — it
  waits for *the whole run*, not just this join. Rejected without reopening
  D-35.
- **Drop the `vector_retriever -> context_fusion` edge and rely on `news`
  being a persisted, already-merged channel by the time `leader_state` alone
  triggers `context_fusion`.** This works for batches with at least one
  candidate that reaches `leader_state`, but a batch where *every* candidate
  drops (no neighbourhood at all) would never trigger `context_fusion` in
  `with_news=True` mode — a new asymmetry against `with_news=False`, where
  today the graph already has this property (its only edge into
  `context_fusion` is `leader_state`'s) but nothing currently exercises or
  relies on the all-drop case. Rejected in favour of the option below, which
  has no such edge case.

### Recommended design (not the literal ask — flagging this explicitly)

Keep `vector_retriever` dispatched from `route_on_neighbourhood`, the exact
same call that dispatches `leader_state` — this is what keeps both in the same
superstep and keeps `context_fusion`'s join safe. What changes: **stop gating
`vector_retriever` on `lag_edges` being non-empty.** Today, a candidate with no
neighbourhood returns bare `END` and neither sibling runs. After this change,
that candidate still returns `END` when `with_news=False` (unchanged, no
regression possible — the constraint on `with_news=False` is satisfied
trivially because this branch of the function is untouched), but when
`with_news=True` it instead returns `[Send("vector_retriever", {"candidate":
c})]` — `leader_state` still does not run for it (D-27's short-circuit is
preserved for the *graph* half), but the *news* half no longer incorrectly
inherits a gate it never needed, per D-83.

This does not give `vector_retriever` true from-`START` concurrency with
`graph_retriever` — it still cannot start until `graph_retriever`'s branch
completes, because that is the only place it can be Sent from without breaking
the join. What it does fix: `vector_retriever` no longer skips a candidate
*because of* the graph's neighbourhood result, which is the actual, provable
falsehood the diagram encodes today (a dependency that D-83 already showed
doesn't exist). The docstring and page copy (PHASE-5/6) say this plainly,
including *why* the scheduling coupling remains — a documented tradeoff, not a
silent inaccuracy.

## Behaviour-change analysis (answers to the brief's four questions)

1. **What reaches `context_fusion` for a dropped candidate, before/after.**
   Before: nothing — the branch returns `END`, `leader_shocks`/`news` are never
   written for that key, and `context_fusion`'s `if not leaders and not
   c.origin_leader: continue` (D-85) skips it anyway since `edges_for` already
   returns `[]` for it via `lag_edges_by_key`. After: `news_by_key[key]` gets a
   (possibly empty) entry from the now-unconditional `vector_retriever` Send,
   but `context_fusion` still hits the identical `continue` — `leaders` is
   still `[]`, `c.origin_leader` is still unset for an externally-sourced
   candidate — so **no `evidence_by_key`/`effective_evidence_by_key`/
   `description_by_key` entry is created either before or after**, and
   `assess()`'s gate (`key not in effective_by_key: continue`) still skips it.
   **The published output (`assessments`) is unaffected.** The only new state
   is a `news_by_key` entry with no matching assessment.
   Checked whether that phantom entry reaches anything: `assess()` and
   `Assessment` never carry raw news at all (`co_mention` `Evidence` is built
   only for candidates that pass the D-85 gate, and `assess()` separately
   strips `co_mention` from `supporting`/`contradicting` regardless — see
   `assessor.py:31`). `serve.py`'s per-assessment SSE payload has no news
   field. The only two consumers of the raw `news` channel are
   `scripts/capture_showcase.py` (writes it to `docs/showcase-trace.json`,
   keyed by candidate, independent of whether that candidate got an
   assessment — not read by `scripts/serve_index.html`, confirmed by grep) and
   `scripts/capture_trace.py`'s `detail()` (feeds a per-node `"event"` SSE
   message that `serve_index.html` has no listener for — confirmed by grep,
   `addEventListener("event", ...)` does not exist in that file). **The
   phantom entry is real but inert on every surface a person actually looks
   at today.** Recorded as an accepted, documented consequence, not silently
   dropped — see the halt condition about it below.

2. **`test_fanout.py::test_candidate_without_neighbourhood_is_skipped`.**
   Unaffected, trivially: its `_invoke` helper calls `build_graph(with_news=False)`
   (`tests/test_fanout.py:39`), and the recommended design changes nothing in
   the `with_news=False` branch of `route_on_neighbourhood`. `vector_retriever`
   is not even a node in that graph (per `test_graph_compiles_both_topologies`).
   All three of its assertions survive unchanged. The three other tests that
   exercise the same D-27 "drops to `END`" path
   (`test_market_scan.py::test_unioning_the_originating_leader_into_signal_universe_closes_the_reentry_hole`,
   `test_runner_marketscan.py::test_run_excludes_originating_leader_from_its_own_candidates_neighbourhood`)
   are likewise all `with_news=False` and unaffected. A **new** test is needed
   to pin the `with_news=True` case, which none of the existing tests cover —
   see PHASE-2.

3. **Cost: one extra vector query per dropped candidate, when `with_news=True`.**
   Bounded already by existing machinery (`RetryPolicy(max_attempts=3)`,
   `timeout=30.0` on `vector_retriever`), so the worst case per dropped
   candidate is no worse than the worst case per already-included candidate
   today. On the one real date measured (2026-05-11), the drop rate was 0/19 —
   one date, stated as such, not extrapolated. **Recommendation: accept the
   cost without a dedicated measurement phase.** The query is a single ANN
   lookup, the count of extra queries is bounded by the batch size (never
   unbounded), and building a drop-rate study to justify a per-query cost this
   small would cost more than it could ever save. This is an explicit
   assumption, not a hidden one — see the halt condition below for what would
   overturn it.

4. **LangGraph semantics.** Addressed above — the recommended design keeps
   `vector_retriever`'s Send inside the *same* `route_on_neighbourhood` call as
   `leader_state`'s, so both still complete in the identical superstep, and
   `context_fusion` still fires exactly once with both branches visible,
   exactly as it does today. Nothing about `CachePolicy` (only on
   `graph_retriever`, untouched), the checkpointer, `interrupt()`/resume
   (`review` reads already-committed `assessments`, unaffected), or checkpoint
   replay changes, because the *set* of nodes and edges is identical before
   and after — only the *content* of one function's return value changes for
   one previously-unreachable case (`lag_edges` empty, `with_news=True`).

## Success criteria
- `route_on_neighbourhood` Sends `vector_retriever` for every candidate when
  `with_news=True`, including ones with no neighbourhood; `leader_state` still
  only Sends for candidates with a neighbourhood (D-27 unchanged).
- `context_fusion` still executes exactly once per batch, proven by a test
  (PHASE-2b/PHASE-3), not merely asserted.
- `with_news=False` is byte-identical to today (no line in
  `route_on_neighbourhood`'s `with_news=False` path changes).
- Full suite green: `uv run pytest`.
- `uv run python scripts/check_baseline.py --synthetic` prints `baseline
  unchanged: 6 rows identical` at every phase boundary from PHASE-3 onward.
- `src/lagmatrix/graph/builder.py`'s module docstring and ASCII diagram, and
  the two identified passages in `scripts/serve_index.html`, no longer imply
  `vector_retriever` depends on `graph_retriever`'s *neighbourhood result* to
  decide whether to run, and explain *why* it still runs one step behind it
  (the join-safety constraint, not a data dependency).
- `docs/spikes/overall.md` has a new D-89 (this finding) and Q-44 (true
  `START`-parallelism, parked) entry.

## PHASE-1 — Log the finding before writing any code
**Exempt from TDD:** pure decision-logging, no behavioural change.
**Completion criterion:** `grep -c "^### D-89" docs/spikes/overall.md` and
`grep -c "^| Q-44" docs/spikes/overall.md` (or wherever the open-questions
table lives) each return `1`, added via the `spike-log` skill (`/spike-log
open` first, per CLAUDE.md).
- TASK-1.1 Record D-89: the join-safety finding above (the probe, its result,
  and the conclusion that literal `START`-fan-out is unsafe under D-35's
  declines), with "Outcome: Accepted — the recommended design in
  PLAN-2026-09-09-news-parallel.md avoids the hazard rather than absorbing it."
- TASK-1.2 Record Q-44: "Is true `START`-level parallelism for
  `vector_retriever` worth redesigning `context_fusion` to be idempotent
  (or reopening D-35 for `defer=True`)?" Blocks: none currently: parked,
  not on this plan's critical path.

## PHASE-2 — TDD red: pin the new behaviour and the join invariant
**Completion criterion:** the two new tests below fail against the
*unmodified* code, for the stated reason, observed via `uv run pytest
tests/test_fanout.py tests/test_news.py -k dropped_candidate_with_news or
context_fusion_runs_once -v` (test names illustrative; pick names that match
each file's existing convention).
- TASK-2.1 (test) In `tests/test_news.py` (extends its existing
  `FakeNewsIndex`/`fake_vector_index` fixtures, `topk=3` convention), add a
  candidate whose symbol is absent from the `closes` fixture (mirrors
  `test_fanout.py`'s `GHOST`), batched alongside `CAND`. Configure
  `FakeNewsIndex` to return one chunk for the ghost symbol too, so a pass
  proves the branch really ran rather than merely defaulting an absent key to
  `[]`. Assert: `candidate_key(ghost) in out["news"]` (fails today — the key is
  never merged in because the branch returns bare `END` and `vector_retriever`
  never Sends for it), `ghost.symbol not in {a.candidate.symbol for a in
  out["assessments"]}`, `CAND` still gets exactly one assessment as before.
- TASK-2.2 (test) In `tests/test_fanout.py`, add a call-counting test for
  `context_fusion` itself, following `test_caching.py`'s `_spy` pattern:
  monkeypatch `lagmatrix.graph.builder.fuse_evidence` with a spy that appends
  to a list before delegating to the real function, run `build_graph(with_news=True)`
  over a batch mixing one dropped candidate (ghost symbol) and one normal
  candidate (`topk=3`, `fake_vector_index` injected), assert `len(calls) == 1`
  after the invoke. This is the test that would have caught the rejected
  from-`START` design (it fails with `len(calls) == 2` under that design,
  proving PHASE-2's tests are not vacuous against the alternative this plan
  rejected) — sanity-check this once by trying the rejected design against it
  locally, then discard that experiment; do not commit it.
- TASK-2.3 Re-run the full existing suite once to confirm nothing already
  fails for unrelated reasons before starting (a clean baseline for the red
  phase): `uv run pytest`.

## PHASE-3 — TDD green: the minimal fix
**Completion criterion:** PHASE-2's two new tests pass; `uv run pytest` is
fully green; `uv run python scripts/check_baseline.py --synthetic` prints
`baseline unchanged: 6 rows identical`.
- TASK-3.1 (impl) In `src/lagmatrix/graph/builder.py`'s `route_on_neighbourhood`,
  move `c = state["candidate"]` (and `key = candidate_key(c)`) above the
  `if not state.get("lag_edges")` check. Change that branch from
  unconditional `return END` to:
  `with_news`-gated — `return [Send("vector_retriever", {"candidate": c})] if
  with_news else END`. No other line in `build_graph` changes. `tdd-green`
  must not touch any test file.

## PHASE-4 — TDD refactor
**Completion criterion:** `uv run pytest` still fully green after any changes
in this phase; if no refactor is warranted, say so and skip — do not refactor
for its own sake (CLAUDE.md §2/§3).
- TASK-4.1 (refactor, conditional) If the two-line conditional inside
  `route_on_neighbourhood` reads awkwardly once written, extract a
  private helper (e.g. `_no_neighbourhood_route(c, with_news)`), tested via
  the same PHASE-2 tests (no new tests needed — this changes structure, not
  behaviour). Expected outcome: likely skipped, the diff is small enough not
  to need it.

## PHASE-5 — `builder.py` docstring and diagram
**Exempt from TDD:** documentation only, no behavioural change (the code from
PHASE-3 already fully describes the real behaviour; this phase makes the
comment match it).
**Completion criterion:** manual diff review confirms every claim below is
addressed; no line outside the module docstring changes.
- TASK-5.1 Update the ASCII diagram to show `vector_retriever` Sent from the
  same per-branch conditional as `leader_state` (already drawn as parallel
  siblings — this was already correct), but add a line noting the `END`
  branch is only reachable when `with_news=False`; when `with_news=True`, a
  candidate with no neighbourhood still reaches `vector_retriever` alone
  (skipping `leader_state`) before `context_fusion`.
- TASK-5.2 Add a paragraph explaining *why* `vector_retriever` is dispatched
  from `graph_retriever`'s conditional rather than `START`, despite having no
  data dependency on it (D-83): the LangGraph join-safety constraint on
  `context_fusion`, citing D-89. State plainly that this is a scheduling
  artefact of the adopted primitives (D-35), not a logical dependency.

## PHASE-6 — `scripts/serve_index.html` copy
**Exempt from TDD:** static prose/documentation, no JS behaviour changes; no
test infrastructure exists or is warranted for this file.
**Completion criterion:** manual diff review confirms only the two passages
below change; `drawTopo()`, `SAYS`, `CHAIN`, and the SVG box/edge geometry are
untouched (they already draw `leader_state`/`vector_retriever` as parallel
siblings under `graph_retriever`, which remains true — verified in this plan's
investigation, not assumed: `LANES` is populated solely from
`graph_retriever` spans, and box positions for both siblings share `Y.leaf`,
both linked from `Y.fan`, independent of this change).
- TASK-6.1 The "dashed line" paragraph (`scripts/serve_index.html` near the
  `<code>Send</code> fan-out` note, currently: *"if a company had no
  neighbours at all, its lane would stop there instead of continuing"*):
  qualify it — only the graph/`leader_state` leg stops; the news leg still
  runs, unconditionally, because it never needed the graph's output.
- TASK-6.2 The `<dt>stop early <code>END</code></dt>` glossary entry
  (currently implies the whole lane stops): reword to match TASK-6.1, and drop
  or rephrase the static "No branch took it this run" claim so it does not
  read as a general guarantee (it is a mode-specific outcome, not description
  of the pipeline overall — the live page runs `with_news=True` whenever a
  GraphRAG DB is reachable, `serve.py:182`, `with_news=db is not None`, in
  which case `END` is no longer reachable at all for the graph-drops-but-news-
  runs case described in TASK-6.1).

## PHASE-7 — Close-out verification
**Completion criterion (all must hold simultaneously):**
- `uv run pytest` — full suite green, no skips newly introduced.
- `uv run python scripts/check_baseline.py --synthetic` → `baseline unchanged:
  6 rows identical`.
- `grep -n "^### D-89\|^### Q-44\|^| Q-44" docs/spikes/overall.md` shows both
  present (from PHASE-1).
- `git diff --stat` touches only: `src/lagmatrix/graph/builder.py`,
  `tests/test_news.py`, `tests/test_fanout.py`, `scripts/serve_index.html`,
  `docs/spikes/overall.md`, this plan file. Anything else in the diff is a
  halt condition (see below).

## Halt conditions
- **Any change outside the five files listed in PHASE-7.** This is a small,
  targeted fix; a larger diff means the design drifted from this plan mid-
  execution and must stop for review before continuing.
- **PHASE-2's tests pass against the unmodified code.** That would mean the
  behaviour this plan targets does not actually reproduce as analysed here —
  stop and re-diagnose rather than deleting/weakening the test to make it red.
- **`check_baseline.py --synthetic` output changes at any point.** In
  `with_news=False` mode nothing here should ever move it; if it moves,
  something outside the intended blast radius was touched.
- **Any of the "must remain untouched" tests need editing**
  (`test_graph_builder.py`, `test_candidate_without_neighbourhood_is_skipped`,
  the two reentry tests, `test_caching.py`, `test_checkpointing.py`,
  `test_review.py`). Their being `with_news=False` (or, for `test_caching.py`,
  entirely orthogonal to `route_on_neighbourhood`'s branching) is this plan's
  whole safety argument; a forced edit there means that argument was wrong.
- **A future attempt to implement the literal from-`START` design.** Re-run
  this plan's probe against whatever `langgraph` version is installed at that
  time first — the finding is about the *installed version's* behaviour, not
  a permanent property of the framework, and could change on upgrade. Do not
  assume either the original brief or this plan's rejection is still correct
  without re-checking.

## What would make this wrong
- If LangGraph's real multi-Send join semantics differ from the toy probe once
  exercised through the *actual* graph shape (N-candidate fan-out, cache,
  retry, checkpointing all present at once) — this is exactly what PHASE-2's
  `context_fusion`-call-count test (TASK-2.2) is for; if it passes at 1 call
  against the unmodified graph already (i.e., the toy probe's finding somehow
  doesn't generalise), the central finding is moot and the literal design
  should be reconsidered — but that would show up as TASK-2.2 not going red
  the way PHASE-2 expects, which is itself the halt condition above.
- If the real production drop rate (candidates with no neighbourhood) turns
  out to be far from zero, the "accept the query cost without measuring"
  judgment in point 3 should be revisited — that would need a new pass over
  more than the one 2026-05-11 date this plan's cost analysis relies on.
- If `capture_showcase.py`'s recorded `news` dict gaining phantom
  no-assessment entries turns out to matter to some consumer this
  investigation didn't find (grep found none) — spot-check
  `docs/showcase-trace.json` once after PHASE-3 by hand rather than assuming
  the grep was exhaustive.
