# PLAN-2026-09-09-ingest-coherence

**Goal:** Make `scripts/daily_ingest.py` actually extend the file co-movement
reads, derive `as_of` from that file only after it has been extended, and turn
a resulting empty edge set into a loud, explicit failure instead of a silent
"done".

**Decisions this depends on:** D-95 (co-movement is built from measured price
history and stored in `moves_with` — the reason `data/bars-10y.parquet` matters
at all), D-93/D-94 (why the long file is a fixed, survivorship-biased universe
distinct from the short file's drifting one — see "Two files, kept" below),
D-97 (loaders upsert and never drop; the same "silent wrong answer" failure
class this plan closes for `node_comovement`), D-100/D-104/D-107/D-108 (this
week's running precedent: a wrong or missing answer delivered without an error
is a defect, fixed by making the failure observable, not by loosening the
check), D-38 (unverifiable is a design defect — redesign for observability
rather than report a limitation), D-16 (point-in-time correctness by
construction — `as_of` ordering is exactly this property).

Not a decision this plan makes, but work it deliberately does not repeat:
`docs/plans/PLAN-2026-09-09-daily-ingest.md`'s own PHASE-2 already specified
`merge_bars`/`daily_watermark`/`scripts/fetch_daily_bars.py` for incremental
bar ingestion and was never executed (`src/lagmatrix/ingest.py` and
`scripts/fetch_daily_bars.py` do not exist — confirmed by `find` before writing
this plan). This plan **completes that spec**, retargeted at
`data/bars-10y.parquet` specifically (that plan left the target file
ambiguous; this incident resolves it — see PHASE-3), rather than inventing a
second design for the same unbuilt piece.

**Open questions that could invalidate it:** none block starting. One new
question is raised and deliberately left open rather than guessed at — see
"Open question: the universe gap" below.

## Facts re-verified before writing this plan (not taken on report)

- `grep -c LONG_BARS scripts/daily_ingest.py` → `1`. Confirmed: the constant is
  defined (`daily_ingest.py:41`) and never read.
- `scripts/fetch_bars.py:28`, `OUT = "data/bars.parquet"` — the only file
  `node_bars` (`daily_ingest.py:99-105`) can possibly write to, since it shells
  out to exactly this script with no arguments.
- `serve.COMOVE_CLOSES()` (`scripts/serve.py:55-72`) prefers
  `data/bars-10y.parquet`, falling back to `data/bars.parquet` then the
  synthetic fixture. `node_comovement` (`daily_ingest.py:124-147`) calls this
  function, so co-movement edges are computed from the long file whenever it
  is present — confirmed, not assumed.
- `serve.default_as_of()` (`scripts/serve.py:75-93`) reads only
  `("data/bars.parquet", SYNTHETIC_CLOSES)` — the long file is not in its
  search path at all, despite the docstring's claim to return "the last
  session actually present in the data". Confirmed by reading the function,
  not by report.
- Measured directly, today: `data/bars.parquet` last session
  `2026-09-09`, 3,201 symbols; `data/bars-10y.parquet` last session
  `2026-09-04`, 2,183 symbols (`uv run python3 -c "pd.read_parquet(...)"`, both
  files, this session).
- `src/lagmatrix/comovement.py:57-78`, `comovement_edges` returns `[]` on two
  branches: `as_of` not present in `closes.index` at all (line 74-75), and
  fewer than `trail` sessions precede it (line 77-78). `upsert_comovement`
  (`src/lagmatrix/adapters/arango.py:99-113`) loops over `edges` and writes
  nothing for an empty list, no error raised either place. Confirmed by
  reading both functions directly — the report's guess was right.
- `tests/` has **no file at all** covering `daily_ingest.py` —
  `grep -rln "daily_ingest\|node_comovement\|node_bars\b" tests/` matches
  nothing. `node_bars`, `node_comovement` and `last_bar_date` have never been
  unit tested, which is how this shipped.
- Current suite, run just now: **216 passed, 0 failed** in 24.7s. This
  supersedes the "210 passed, 6 failed" figure in the assignment — the Q-47
  cycle (`D-108`) has since landed and closed clean. Stated so the next person
  does not go looking for 6 failures that are not there.
- `docs/plans/PLAN-2026-09-09-homelab-deploy.md` PHASE-3 (read in full before
  writing this plan, per instruction) is **written but unexecuted** —
  confirmed: `COMOVE_CLOSES()` today still takes zero arguments and
  `InsufficientHistoryError` does not exist anywhere in the tree. PHASE-3's
  scope, once it runs, is a **pyarrow-pushdown windowed read** of
  `COMOVE_CLOSES` plus an LRU-of-1 cache, for resident-memory reduction on the
  deployed pod — a read-side, deployment-motivated change, distinct from this
  plan's write-side, correctness-motivated one. See "Coordination with
  PHASE-3" under PHASE-1 for exactly where the two would otherwise collide and
  how this plan avoids it.

## Two files, kept — not merged

`data/bars.parquet` (3,201 symbols, ~159 sessions, refetched in full and
liquidity-filtered against *today's* window every run — `fetch_bars.py:80-105`)
and `data/bars-10y.parquet` (2,183 symbols, 2,514 sessions, a **fixed**
universe selected once at generation time — `fetch_history.py`'s own docstring:
"liquidity is measured on the recent window... the constituent list carries
survivorship and look-ahead") serve different, incompatible contracts, not one
accidentally split in two:

- **Reproducibility.** `check_baseline.py`, `capture_baseline.py`,
  `capture_showcase.py`, `capture_trace.py`, `run_pipeline.py`, `load_edgar.py`,
  `load_news.py` and `fetch_history.py` itself all read `data/bars.parquet` as
  a fixed input to numbers already published (`grep -rn "bars\.parquet"
  scripts/*.py`, ten call sites, checked directly). `PLAN-2026-09-09-daily-
  ingest.md` PHASE-2 already ruled on this once, in writing, before this
  incident: "silently growing them would break reproducibility of numbers
  already recorded against their current contents" — the same lesson Q-27
  recorded. That ruling is adopted here rather than re-derived.
- **Universe semantics.** `bars.parquet`'s universe *drifts* — it is
  whatever clears the liquidity filter on the day `fetch_bars.py` last ran.
  `bars-10y.parquet`'s universe was *fixed* once, deliberately, so that
  D-93/D-94/D-95/D-96/D-100's discovery/validation split measures the same
  population across the whole ten-year window. Merging the files would force
  one of these two contracts to break: either the long file's fixed
  population starts drifting (invalidating every one of those five
  decisions' methodology, which explicitly relies on a stable population), or
  the short file's fast, small read balloons to carry ten years of history
  every reproducibility-pinned script does not need.
- **Consumption shape.** The long file is already the one `homelab-deploy`
  PHASE-3 is redesigning to a *windowed* read specifically because reading it
  in full costs 876MiB resident (measured in that plan). Coupling the two
  files would add the short file's ~3,201-symbol, actively-changing column
  set on top of that, working directly against the memory reduction PHASE-3
  exists to deliver.

**Decision: keep both files. This plan extends `data/bars-10y.parquet`'s
history only, in place, with its existing column set unchanged. It does not
touch `data/bars.parquet`, `data/bars-5y.parquet`, or either file's universe.**

## Open question: the universe gap

`data/bars.parquet` carries 3,201 symbols against `data/bars-10y.parquet`'s
2,183 — roughly 1,000 more. I cannot determine from the code or the decision
log whether this specific, and apparently growing, gap is an intended
consequence of the two files' different selection methodologies (drifting
liquidity filter vs. a filter applied once years ago) or a symptom that should
trigger a universe refresh. `PLAN-2026-09-09-daily-ingest.md` PHASE-7,
TASK-7.3(a) already logged "universe refresh cadence for `data/bars-10y.parquet`"
as an open question and marked it explicitly out of scope; that question is
unanswered, not superseded, and this plan does not answer it either — raised
again here rather than silently assumed. **This plan does not grow
`bars-10y.parquet`'s symbol set.** Log via `spike-log` in PHASE-5 rather than
deciding here.

## Success criteria

1. `uv run pytest -q` passes with no reduction from today's measured baseline
   of **216 passed, 0 failed**; every new test in this plan adds to that
   count.
2. `grep -n 'LONG_BARS' scripts/daily_ingest.py` shows **3 or more**
   occurrences (today: 1 — the unused definition). The regression guard for
   the exact defect this plan closes.
3. `uv run python scripts/daily_ingest.py --dry-run` exits 0 and its printed
   plan names a bars-extension step for the long file, distinct from the
   existing short-file `bars:` line.
4. A test asserts `serve.default_as_of.__doc__` and `serve.COMOVE_CLOSES`'s
   own path preference agree in order — concretely, both functions' first
   candidate path is `data/bars-10y.parquet`.
5. A test constructs a `closes` frame whose last session predates a requested
   `as_of` and calls `node_comovement` on it directly; asserts the returned
   dict has a non-empty `"errors"` list and an empty (or absent) `"done"`
   list — the literal shape of today's silent "0 edges upserted" success,
   now failing loudly. This is the falsifiable reproduction of the actual
   incident, not a proxy for it.
6. Operational (needs live Alpaca credentials, not part of the automated
   suite, same category as every other bars-fetching script in this repo):
   running `scripts/fetch_daily_bars.py` twice back-to-back produces
   `0 new rows` on the second run.

---

## PHASE-1 — Loud failure when co-movement's `as_of` has no usable history

**Depends on:** nothing else in this plan. Ships first and independently: even
before PHASE-3/4 land, this converts today's exact defect (a stale
`bars-10y.parquet` silently producing "0 edges upserted, done") into a
reported error, on the code as it exists right now.

**Coordination with `homelab-deploy` PHASE-3, stated so the two do not
collide:** PHASE-3 will eventually make `comovement_edges`/`COMOVE_CLOSES`
themselves raise on insufficient history, across all three of `serve.py`'s
call sites (`movers`, `followers`, `network`) plus `daily_ingest.py`. That is
a wider, deployment-motivated change this plan does not make. This phase adds
one small, **additive** function — it does not change `comovement_edges`'s
existing return-`[]` contract, so it cannot conflict with PHASE-3's later
change to that contract. When PHASE-3 executes, its author should grep for
`session_available` (added below) before inventing a second helper for the
same check, and may retire this phase's manual pre-check in `node_comovement`
once `comovement_edges` raises on its own — noted as a follow-up cleanup, not
built here.

**Halt condition specific to this phase:** before writing TASK-1.1, run
`grep -n "InsufficientHistoryError\|def COMOVE_CLOSES" scripts/serve.py
src/lagmatrix/comovement.py`. If either name already exists, `homelab-deploy`
PHASE-3 has landed since this plan was written — stop, re-read that phase's
current state, and rescope this phase to use what it already built instead of
adding a second mechanism.

**Completion criterion:** `uv run pytest -q tests/test_daily_ingest.py -q`
and `uv run pytest -q tests/test_comovement.py -q` both pass; success
criterion 5 above is one of the new tests.

- TASK-1.1 (test). Create `tests/test_comovement.py` if it does not already
  exist, or add to it if it does (`grep -n "^import\|^from" src/lagmatrix
  /comovement.py` first, to confirm current exports). Import
  `session_available` from `lagmatrix.comovement` — does not exist yet,
  observe `ImportError` before TASK-1.2.
  - `test_session_available_true_when_trail_sessions_precede_as_of`: a
    synthetic `closes` DataFrame with 300 daily rows; `as_of` at row 280
    (`trail=250` sessions available before it) → `(True, "")`.
  - `test_session_available_false_when_as_of_is_absent`: `as_of` one day past
    the frame's last index entry → `(False, ...)`, message names the
    requested date and the frame's actual last session.
  - `test_session_available_false_when_trail_sessions_do_not_precede_as_of`:
    `as_of` at row 10 of a 300-row frame, `trail=250` → `(False, ...)`,
    message names how many sessions actually precede it (10, not 250).
- TASK-1.2 (impl). `src/lagmatrix/comovement.py`: add
  `session_available(closes: pd.DataFrame, as_of: date, trail: int) ->
  tuple[bool, str]`, reusing the exact lookup `comovement_edges` already does
  internally (`np.where(closes.index.date == as_of)`) — read-only, no change
  to `comovement_edges` itself.
- TASK-1.3 (test). Create `tests/test_daily_ingest.py`. Import `node_comovement`
  from `daily_ingest` (via the existing `_scripts_on_path`-style fixture —
  match `tests/test_scripts_import.py`'s pattern of inserting `scripts/` onto
  `sys.path`). Monkeypatch `serve.arango_db` to return a fake db object with
  a no-op `.aql`/collection surface sufficient for `upsert_comovement` to run
  against (or monkeypatch `lagmatrix.adapters.arango.upsert_comovement`
  directly to a spy — simpler, and this task only needs to prove it is
  **not called** on the failure path), and monkeypatch `serve.COMOVE_CLOSES`
  to return a small synthetic frame whose last session is `2026-09-04`.
  - `test_node_comovement_reports_an_error_when_as_of_exceeds_the_data`:
    call `node_comovement({"as_of": "2026-09-09", "dry_run": False})`
    (`as_of` five sessions past the fake frame's last date). Assert the
    returned dict's `"errors"` is non-empty and names `2026-09-09` and
    `2026-09-04`; assert `upsert_comovement` (spied) was never called. This
    is success criterion 5, and the literal reproduction of today's incident
    on synthetic data.
  - `test_node_comovement_reports_an_error_on_insufficient_trailing_history`:
    same fixture, `as_of` present in the frame but fewer than `trail=250`
    sessions before it. Same assertions.
  - `test_node_comovement_upserts_normally_when_history_is_sufficient`:
    negative control — a fixture where `as_of` has the full trail available;
    assert `"done"` is non-empty, `"errors"` is empty, and `upsert_comovement`
    was called once. Without this, a `session_available` that always returns
    `False` would pass the two tests above.
- TASK-1.4 (impl). `scripts/daily_ingest.py`, `node_comovement`: after
  computing `closes = serve.COMOVE_CLOSES()` and before calling
  `comovement_edges`, call `ok, reason = session_available(closes, d,
  trail=250)`; if not `ok`, `return {"errors": [f"comovement: {reason}"]}`
  without calling `comovement_edges` or `upsert_comovement`. Import
  `session_available` alongside the existing `comovement_edges` import.
- TASK-1.5 (verify). `uv run pytest -q` — count increases by exactly 6 over
  the 216-passed baseline (3 from TASK-1.1, 3 from TASK-1.3).

**What would make this wrong:** if a legitimately quiet trading day produces
zero edges above `min_abs_corr=0.5` *with* a fully-available trailing window —
that is not the failure this phase targets (D-95 measured ~23,855 edges as
the norm, so this is vanishingly unlikely but not impossible) and must **not**
be caught by this phase's check, which only fires on the two data-availability
conditions (`as_of` absent; insufficient trail), never on "edges computed
correctly and there happen to be few or none." `session_available` is checked
before `comovement_edges` runs at all, so it cannot see or react to the edge
count — this is what keeps the two cases apart.

---

## PHASE-2 — `default_as_of()` reads what co-movement reads

**Status: DONE, executed 2026-09-09 ahead of this plan** — the broken default
date was live and user-visible, so this phase was run as its own red/green cycle
before the plan was written. See D-109. `default_as_of()` now derives from
`COMOVE_CLOSES()`; tests in `tests/test_serve_default_as_of_coherence.py`.
Do not execute this phase again.

**Depends on:** nothing else in this plan.

**Completion criterion:** `uv run pytest -q tests/test_scripts_import.py -q`
passes with the new test below; `serve.default_as_of()`'s docstring states
its actual search order.

- TASK-2.1 (test). Add to `tests/test_scripts_import.py`:
  `test_default_as_of_prefers_the_long_bars_file`. Monkeypatch
  `pandas.read_parquet` (or, simpler and more targeted, monkeypatch
  `pathlib.Path.exists`/write two small temp parquet files and pass their
  paths — pick whichever the existing test file's style favors; check
  `tests/test_scripts_import.py`'s existing fixtures before choosing) so that
  a call for `data/bars-10y.parquet` returns a frame whose last session is
  `2026-09-04` and a call for `data/bars.parquet` returns one whose last
  session is `2026-09-09`. Assert `serve.default_as_of()` returns
  `"2026-09-04"` — the long file's date, not the short file's, proving the
  fallback order actually prefers the long file rather than merely
  co-existing with it. **Observe this fail first** — today it returns
  `"2026-09-09"`.
- TASK-2.2 (impl). `scripts/serve.py`, `default_as_of()`: change the search
  tuple from `("data/bars.parquet", SYNTHETIC_CLOSES)` to
  `("data/bars-10y.parquet", "data/bars.parquet", SYNTHETIC_CLOSES)` —
  identical order to `COMOVE_CLOSES()`. Correct the docstring's claim: state
  plainly that it returns the last session in whichever file co-movement
  itself reads, in the same order, and that this is deliberate (a caller
  asking "what's the default date" and a caller asking "what data is there
  to compute co-movement on" must agree, or the page shows a date its own
  features cannot answer for — today's live defect).
- TASK-2.3 (verify). `uv run pytest -q` — count increases by exactly 1 over
  PHASE-1's running total. Manually, if `--allow-real` credentials are
  available in this environment: `uv run python scripts/serve.py
  --allow-real`, confirm `/config`'s `default_as_of` now reads `2026-09-04`
  (matching `bars-10y.parquet`'s current last session, not `bars.parquet`'s
  `2026-09-09`) — the direct fix for the live-page defect the assignment
  named ("PANW 2026-09-09 -> 0 followers").

**What would make this wrong:** if some caller of `default_as_of()` actually
wants "the freshest date across any source", not "the date co-movement can
answer for" — no such caller was found (`grep -n "default_as_of()"
scripts/serve.py` — every call site feeds it straight into a co-movement or
graph-traversal endpoint), but if one turns up during execution, stop and
re-read this phase rather than special-casing it silently.

---

## PHASE-3 — Extend `data/bars-10y.parquet` incrementally

**Depends on:** nothing else in this plan (parquet only).

**Completes, retargeted:** `PLAN-2026-09-09-daily-ingest.md` PHASE-2's
`merge_bars`/`daily_watermark`/`fetch_daily_bars.py` spec, which named no
concrete output file. This plan names it: `data/bars-10y.parquet`, per "Two
files, kept" above. The task list below is that plan's, carried over rather
than re-derived, with the target file made explicit.

**Completion criterion:** `uv run pytest -q tests/test_ingest.py -q` all pass.
Operational (needs live Alpaca credentials): running
`scripts/fetch_daily_bars.py` twice back-to-back prints `0 new rows` the
second time.

The unit of incrementality is a trading-session date across the whole file,
not per-symbol (bars for one universe arrive on one calendar) — matching the
already-written rationale in `PLAN-2026-09-09-daily-ingest.md` PHASE-2, not
repeated here. The existing ~2,183-symbol column set is reused as-is; no
universe refresh (see "Open question: the universe gap").

- TASK-3.1 (test). Create `tests/test_ingest.py`, importing `merge_bars` and
  `daily_watermark` from `lagmatrix.ingest` — module does not exist yet,
  observe `ImportError` first.
  - `test_merge_bars_dedupes_by_symbol_and_timestamp`: an "existing" frame
    and a "new" frame share one `(symbol, timestamp)` row with a different
    `close`; the merged frame has one row for that key, carrying the new
    frame's value.
  - `test_merge_bars_is_a_noop_on_identical_rerun`: `merge_bars(df, df)` has
    the same row count and values as `df`.
  - `test_daily_watermark_is_the_max_timestamp_present`: pins the contract
    `fetch_daily_bars.py`'s `--since` is built from.
  - `test_daily_watermark_on_empty_frame_returns_none`: an empty DataFrame
    (the very first run) returns `None`, not `NaT`, not an exception.
- TASK-3.2 (impl). `src/lagmatrix/ingest.py`:
  `merge_bars(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame`
  (`pd.concat` + `drop_duplicates(subset=["symbol", "timestamp"],
  keep="last")`, sorted); `daily_watermark(df: pd.DataFrame) -> date | None`.
- TASK-3.3 (script exemption — I/O wiring calling already-tested pure
  functions, matching this repo's own precedent for every other bars-fetching
  script). `scripts/fetch_daily_bars.py`: read `data/bars-10y.parquet` if
  present (else an empty frame), compute the watermark via
  `daily_watermark`, fetch Alpaca daily bars for the file's *existing*
  symbol columns from `watermark + 1 day` through today (print `0 new rows`
  and skip the API call entirely if the watermark is already the last
  completed session), merge via `merge_bars`, write back through a
  temp-file-then-rename so a crash mid-write leaves the previous file
  untouched. Add `"fetch_daily_bars"` to `tests/test_scripts_import.py`'s
  `SCRIPTS` parametrize list (`tests/test_scripts_import.py:33`).
- TASK-3.4 (verify). `uv run pytest -q` — count increases by exactly 4 over
  PHASE-2's running total. Operational, if Alpaca credentials are available:
  run `scripts/fetch_daily_bars.py` twice, confirm `0 new rows` on the
  second call and that `data/bars-10y.parquet`'s last session has advanced
  to at least yesterday's close on the first.

**What would make this wrong:** Alpaca revising a previously-reported close
after the watermark has advanced past that date — `merge_bars`'s
`keep="last"` only helps within one run's own overlap, not a correction
arriving after the fact. Named here, not silently accepted, matching the
same caveat the original PHASE-2 spec already recorded. Separately: if a
real run's peak memory is not "read the existing ~2,514-session file once,
append a handful of new sessions, write once" — i.e., materially larger than
one full read of the current file — stop before wiring this into any
CronJob resource limit `homelab-deploy` PHASE-5 sets, since that limit was
sized without this script existing.

---

## PHASE-4 — `as_of` is derived after the long file is extended, never before

**Depends on:** PHASE-1 (loud failure helper), PHASE-2 (`default_as_of()`
prefers the long file), PHASE-3 (`fetch_daily_bars.py` exists to be wired in).

**Completion criterion:** `uv run pytest -q tests/test_daily_ingest.py -q`
all pass, including a test that a fixed, injected `as_of` bypass reads the
freshly-extended file, not a value captured before it.

- TASK-4.1 (test). Add to `tests/test_daily_ingest.py`. Import `node_bars`
  is already covered implicitly; add tests for a new `node_long_bars`
  (does not exist yet — observe `ImportError` first).
  - `test_node_long_bars_skips_the_fetch_when_already_current`: monkeypatch
    a fake `long_bar_date()` returning yesterday's date; assert
    `node_long_bars({"dry_run": False})`'s `"done"` says "already current"
    and the (monkeypatched, spied) `_run` is never called.
  - `test_node_long_bars_shells_out_to_fetch_daily_bars_when_stale`:
    monkeypatch `long_bar_date()` returning a date more than one day old;
    assert the spied `_run` is called with a command naming
    `scripts/fetch_daily_bars.py`.
- TASK-4.2 (impl). `scripts/daily_ingest.py`: add
  `LONG_BARS = "data/bars-10y.parquet"` is already defined (line 41) — wire
  it to actual use. Add `long_bar_date() -> date | None`, mirroring
  `last_bar_date()` (lines 55-60) but reading `LONG_BARS`. Add
  `node_long_bars(state)`, mirroring `node_bars`'s freshness-check shape
  (lines 99-105) but calling `scripts/fetch_daily_bars.py` and checking
  `long_bar_date()`.
- TASK-4.3 (test). Add
  `test_as_of_is_resolved_after_the_long_bars_file_is_extended` to
  `tests/test_daily_ingest.py`: monkeypatch `serve.default_as_of` to return
  a value that changes between two calls (a small counter/fake simulating
  "before extension" vs. "after"), invoke the graph's node sequence directly
  in order (`node_long_bars` then `node_comovement`, not through
  LangGraph — plain function calls, since both are already plain functions
  taking a state dict), and assert `node_comovement` used the *second*
  (post-extension) value, not the first. This is the concrete,
  falsifiable form of "derived after, not before" — the literal ordering
  bug in today's `main()`.
- TASK-4.4 (impl). `scripts/daily_ingest.py`:
  - `build()`: add `g.add_node("long_bars", node_long_bars,
    retry_policy=retry)`; change the edge `bars -> comovement` to
    `bars -> long_bars` and add `long_bars -> comovement`.
  - `node_comovement`: change `d = date.fromisoformat(state["as_of"])`
    (current line 144) to `d = date.fromisoformat(state.get("as_of") or
    serve.default_as_of())` — resolved here, after `node_long_bars` has run
    (guaranteed by the new edge), not in `main()`.
  - `main()`: change `as_of = args.as_of or serve.default_as_of()` to
    `as_of = args.as_of` (may be `None` — an explicit operator override
    passes through unchanged; the common case defers resolution to
    `node_comovement`). Change the printed banner from
    `f"daily ingest, as of {as_of}..."` to name the override if given, or
    state plainly that the date is resolved after the bars fetch — do not
    print a guessed date that the run may not actually use.
- TASK-4.5 (verify). `uv run pytest -q` — count increases by exactly 5 over
  PHASE-3's running total (2 from TASK-4.1, 3 from TASK-4.3's set — adjust
  if TASK-4.3 is split into more than one assertion-per-test; state the
  actual count when executed, per this repo's habit of citing the measured
  number). `uv run python scripts/daily_ingest.py --dry-run` — exits 0,
  prints a `long_bars:` line, and prints the banner's new, honest wording.
  This is success criterion 3.

**What would make this wrong:** if `node_long_bars`'s dry-run path
(`state.get("dry_run", False)`) is not honored identically to `node_bars`'s —
a `--dry-run` invocation must never shell out to `fetch_daily_bars.py` for
real, matching the existing `_run` helper's own dry-run contract.

---

## PHASE-5 — Decision log and close-out

**Docs-only, no TDD.**

- TASK-5.1. Check `docs/spikes/overall.md`'s current maximum `D-nn` before
  writing (this session alone advanced it to D-108 while this plan was being
  written — do not hardcode a number). Via `spike-log`, append one decision
  covering: the long file was never extended and `as_of` was derived from
  the wrong file before the fetch ran; the fix (extend `bars-10y.parquet`
  incrementally, resolve `as_of` after extension, fail loudly on
  insufficient history); and the decision to keep the two bars files
  separate rather than merge them.
- TASK-5.2. Log the universe-gap question (3,201 vs. 2,183 symbols) as an
  open `Q-nn`, referencing `PLAN-2026-09-09-daily-ingest.md` PHASE-7
  TASK-7.3(a) rather than duplicating it if that question is still open
  under its own number — check first.
- TASK-5.3. If `README.md` describes `daily_ingest.py` as extending the long
  bars file already, or names a fixed `as_of` computed before the fetch,
  correct only those sentences (CLAUDE.md: touch only what you must).

## Halt conditions

- If `grep -n "InsufficientHistoryError\|def COMOVE_CLOSES" scripts/serve.py
  src/lagmatrix/comovement.py` shows either name already exists before
  PHASE-1 starts, stop and rescope PHASE-1 against `homelab-deploy` PHASE-3's
  actual, landed shape rather than adding a second mechanism for the same
  check.
- If PHASE-3's real-Alpaca run (TASK-3.4) shows `scripts/fetch_daily_bars.py`
  needing to change `data/bars-10y.parquet`'s symbol columns — a new IPO's
  bars arriving, or a delisted symbol Alpaca no longer serves — stop. That is
  the universe-refresh question this plan explicitly does not answer, and
  the fix is not "merge whatever columns show up."
- If TASK-4.3 cannot demonstrate `as_of` changing between "before" and
  "after" `node_long_bars` runs without also changing `serve.default_as_of`'s
  real implementation in a way TASK-2.1 did not anticipate, stop — it means
  PHASE-2 and PHASE-4 disagree about what `default_as_of()` returns and that
  needs resolving before either is called done.
- If fixing `default_as_of()`'s search order (PHASE-2) changes a
  previously-published number for a date **both** files claim to have (not
  merely extending which dates are answerable), stop — that means the two
  files disagree about a session they both cover, which is a data
  correctness problem, not an ordering one, and is out of this plan's scope
  to fix blind.
