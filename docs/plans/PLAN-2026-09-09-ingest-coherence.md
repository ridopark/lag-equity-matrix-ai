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
class this plan closes for `node_comovement`), D-100/D-104/D-107/D-108/D-109
(this week's running precedent: a wrong or missing answer delivered without an
error is a defect, fixed by making the failure observable, not by loosening the
check), D-38 (unverifiable is a design defect — redesign for observability
rather than report a limitation), D-16 (point-in-time correctness by
construction — `as_of` ordering is exactly this property).

Not a decision this plan makes, but work it deliberately does not repeat:
`docs/plans/PLAN-2026-09-09-daily-ingest.md`'s own PHASE-2 already specified
`merge_bars`/`daily_watermark`/`scripts/fetch_daily_bars.py` for incremental
bar ingestion and was never executed (`src/lagmatrix/ingest.py` and
`scripts/fetch_daily_bars.py` did not exist — confirmed by `find` before writing
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
- `serve.default_as_of()` (`scripts/serve.py:75-93`, at the time this plan was
  first written) read only `("data/bars.parquet", SYNTHETIC_CLOSES)` — the
  long file was not in its search path at all, despite the docstring's claim
  to return "the last session actually present in the data". **Since fixed —
  see PHASE-2's status note.**
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
- `tests/` had **no file at all** covering `daily_ingest.py` when this plan
  was written — `grep -rln "daily_ingest\|node_comovement\|node_bars\b"
  tests/` matched nothing. `node_bars`, `node_comovement` and `last_bar_date`
  had never been unit tested, which is how this shipped.
- Current suite, run just now: **216 passed, 0 failed** in 24.7s, at the
  moment this plan was first written. This superseded the "210 passed, 6
  failed" figure in the original assignment — the Q-47 cycle (`D-108`) had
  already landed and closed clean. (This plan is now itself mid-execution by
  others; re-run before trusting any absolute count — see PHASE-3's own note.)
- `docs/plans/PLAN-2026-09-09-homelab-deploy.md` PHASE-3 (read in full before
  writing this plan, per instruction) is **written but unexecuted** —
  confirmed: `COMOVE_CLOSES()` still takes zero arguments and
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
   occurrences (originally: 1 — the unused definition). The regression guard
   for the exact defect this plan closes.
3. `uv run python scripts/daily_ingest.py --dry-run` exits 0 and its printed
   plan names a bars-extension step for the long file, distinct from the
   existing short-file `bars:` line.
4. `serve.default_as_of()` and `serve.COMOVE_CLOSES()` are proven, by a test,
   to agree by construction rather than by coincidence — satisfied by
   PHASE-2/D-109; not re-litigated here.
5. A test constructs a `closes` frame whose last session predates a requested
   `as_of` and calls `node_comovement` on it directly; asserts the returned
   dict has a non-empty `"errors"` list and an empty (or absent) `"done"`
   list — the literal shape of today's silent "0 edges upserted" success,
   now failing loudly. This is the falsifiable reproduction of the actual
   incident, not a proxy for it.
6. Operational (needs live Alpaca credentials, not part of the automated
   suite, same category as every other bars-fetching script in this repo):
   running `scripts/fetch_daily_bars.py` twice back-to-back produces
   `0 new rows` and `0 symbols refetched for a split` on the second run.

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

**Status: DONE, executed 2026-09-09 ahead of this plan.** The broken default
date was live and user-visible, so this phase was run as its own red/green
cycle before the rest of this plan was written, and taken further than the
original TASK-2.1/2.2 draft below proposed. See D-109:
`default_as_of()` now derives its answer directly from `COMOVE_CLOSES()`'s own
last session — `return str(closes.index[-1].date())` — rather than
independently re-reading a duplicated path-preference tuple. That is a
stronger fix than this phase originally specified (agreement **by
construction**, not by two lists of paths kept in sync by hand), and is
adopted as-is. Tests in `tests/test_serve_default_as_of_coherence.py`
(confirmed present). **Do not execute this phase's TASK-2.1/2.2 again** — kept
below only as the historical record of what was asked for and superseded by
what was actually built.

**Depends on:** nothing else in this plan.

**Completion criterion (satisfied):** `uv run pytest -q
tests/test_serve_default_as_of_coherence.py -q` passes; `default_as_of()`'s
docstring states its actual, now-guaranteed behaviour.

- ~~TASK-2.1 (test)~~ — superseded. Original draft: monkeypatch two parquet
  reads and assert `default_as_of()` prefers the long file's date. What
  shipped instead directly asserts agreement with `COMOVE_CLOSES()`, which
  subsumes this.
- ~~TASK-2.2 (impl)~~ — superseded. Original draft: change the search tuple to
  `("data/bars-10y.parquet", "data/bars.parquet", SYNTHETIC_CLOSES)`. What
  shipped instead removes the independent search entirely in favor of reading
  `COMOVE_CLOSES()`'s own index — see D-109's stated reason for preferring
  this over the tuple fix.
- TASK-2.3 (verify — satisfied). `uv run pytest -q` reflects the new tests.
  Manually, if `--allow-real` credentials are available: `/config`'s
  `default_as_of` now agrees with `COMOVE_CLOSES()`'s last session — the
  direct fix for the live-page defect the assignment named ("PANW 2026-09-09
  -> 0 followers").

**What would make this wrong:** unchanged from the original draft — no caller
was found wanting "the freshest date across any source" rather than "the date
co-movement can answer for"; re-check this if one turns up.

---

## PHASE-3 — Extend `data/bars-10y.parquet` incrementally

**Revised in place, 2026-09-09, after review — read this before executing
TASK-3.5 below.** The original version of this phase appended new sessions to
`data/bars-10y.parquet` unconditionally. Team-lead review found the flaw
before `scripts/fetch_daily_bars.py` was written (confirmed: it still does not
exist at time of this revision). TASK-3.1/3.2 below (`merge_bars`,
`daily_watermark`) are **already built** — `src/lagmatrix/ingest.py` and
`tests/test_ingest.py` exist and pass, and are unaffected by this revision;
they are pure merge/watermark logic, and the flaw is entirely in what gets
fetched and handed to them, not in the merge itself.

**The flaw, measured, not theorised.** Both `fetch_bars.py:70` and
`fetch_history.py:53` fetch with `adjustment="all"`, and Alpaca
**retroactively re-adjusts a symbol's entire price history** whenever it
splits — confirmed directly against `data/bars-10y.parquet`: NVDA closes
120.79 (2024-06-06), 120.68 (2024-06-07), 121.58 (2024-06-10, the 10:1 split
date), 120.71 (2024-06-11) — smooth, because the whole file was fetched in
one pass and is therefore internally consistent on one adjustment basis.
`merge_bars`'s own `keep="last"` dedup only resolves rows present in **both**
`existing` and `new` on a shared `(symbol, timestamp)` key. An append fetching
only sessions *after* `watermark` never re-requests the already-stored rows at
all, so if a symbol splits between two runs, the append lands on the
post-split basis while every already-stored row for that symbol stays on
whatever basis was in force when it was last fetched — a silent, permanent
step at the append boundary equal to the split ratio, which `keep="last"`
cannot see because there is no overlapping key for it to resolve. D-100's
`_IMPLAUSIBLE_RETURN_CUTOFF = 10.0` does not catch it either: a 10:1 split
boundary reads as a **−90%** single-session return, comfortably inside a
cutoff built to catch >1000% jumps. Left unfixed, this would have been
D-100's exact failure — a wrong number nobody double-checked — reintroduced
by this plan's own new code, silently, the first time any of 2,183 symbols
next splits.

**The fix uses Alpaca's own corporate-actions record, not a statistical
inference.** Checked before recommending it, per the four questions raised:

1. **Is there a cheaper, more direct mechanism than fetching an overlap window
   and comparing closes?** Yes. The installed `alpaca-py` exposes
   `alpaca.data.historical.corporate_actions.CorporateActionsClient` and
   `alpaca.data.requests.CorporateActionsRequest` (confirmed by reading
   `.venv/lib/python3.12/site-packages/alpaca/data/historical/
   corporate_actions.py` and `.../models/corporate_actions.py` directly — not
   yet exercised against the live API in this environment, which has no
   Alpaca credentials; TASK-3.7 below is the operational check). It returns,
   per symbol and date range, a typed record of what actually happened —
   `forward_splits` / `reverse_splits` / `unit_splits` (among others), each
   carrying an authoritative `ex_date` (or `effective_date` for unit splits)
   and the split ratio. This replaces an inferred, epsilon-tuned guess from
   comparing two fetches of the same days with Alpaca's own record of what it
   re-adjusted for. **No overlap-of-bars fetch is needed at all** — this
   supersedes that part of the original ask.
2. **What window, and why.** Query corporate actions for the file's existing
   symbols from `watermark - 5 calendar days` through `today`. The lower bound
   is **not** a gap-coverage number: the query already reaches back to
   `watermark` itself, however stale it is, so a long ingest outage is covered
   automatically — unlike a fixed-session bar-overlap approach, this is not
   bounded by a session count at all. The 5-day pad exists only to cover a
   narrower risk: forward/reverse splits are dated by `ex_date` while unit
   splits use `effective_date`, and this design does not assume either field
   lines up exactly with the day a price jump shows up in daily bars. Five
   calendar days is more than a weekend's margin against that field
   ambiguity, and cheap, since it is one metadata query, not a second bars
   fetch — matching this repo's own habit of a small, stated multiple rather
   than a bare guess (`fetch_bars.py`'s `PAD_BEFORE`/`PAD_AFTER`,
   `homelab-deploy`'s `trail * 2 + 10`).
3. **Refetch inline, or fail loudly?** Refetch inline, automatically, for the
   specific symbol(s) a split is detected for — and **report** it, not hide
   it. A nightly, unattended job (D-97's whole point) cannot pause for an
   operator every time one of ~2,183 symbols splits, which happens routinely;
   refetching one symbol's full ~2,500-row history is cheap regardless of the
   whole file's size, unlike the full-universe nightly refetch already
   rejected as too costly for an unrelated reason (network time, peak
   memory). Fail loudly only if the targeted refetch itself comes back empty
   or errors — a genuine operational failure, not an expected, self-correcting
   event. Matching D-100's own precedent ("the page now explains the
   correction rather than silently swapping the digit"), the ingest's `done`
   report must name which symbols were refetched and why.
4. **Is `data/bars.parquet` immune, and does that argue for periodically
   refetching the long file too?** Verified, not assumed: `fetch_bars.py:
   80-105`'s `main()` refetches its entire window every run and calls
   `bars.to_parquet(OUT, index=False)` — an unconditional full overwrite,
   never an append. It is immune for exactly that reason: the whole file is
   always fetched and adjusted together, so no append boundary can exist —
   the immunity is a property of full-refetching, not of anything clever
   about that script.
5. **Given that, is a periodic full refetch of the long file the better
   answer instead of per-symbol detection? Argued explicitly, not assumed
   away.** Team-lead measured a real cost baseline rather than an estimate:
   `data/bars.parquet` is 509,447 rows over ~159 sessions/3,202 symbols,
   refetched in full every night already; `data/bars-10y.parquet` is
   4,732,255 rows over 2,514 sessions/2,183 symbols — about 9x the rows for
   about 16x the sessions, so a *weekly* full refetch (not nightly — nightly
   was already rejected on cost) is not obviously impossible. Weighed against
   per-symbol corporate-actions detection on three grounds:
   - **Cost.** Incremental-plus-detection touches, every night, only a
     handful of new sessions for ~2,183 symbols plus a metadata query, and
     touches a symbol's *full* history only on the rare night it actually
     splits. A weekly full refetch touches all 4,732,255 rows on a schedule
     disconnected from whether anything changed, every single week, forever.
     Cheaper in aggregate, by a wide margin, even against a once-weekly
     cadence.
   - **Consistency with this week's own precedent.** PHASE-1 (already green)
     establishes that a data problem becomes an explicit, immediate `errors`
     entry, not something left to self-heal. A weekly-refetch-*only* design
     (no nightly detection) would accept up to **six days** of silently
     wrong correlations feeding into `moves_with` before the next refetch
     quietly overwrites them — nobody would ever know those six days were
     wrong. That is D-100's exact failure with a bound on it instead of an
     absence of one; bounded-but-silent is still silent, and it is the
     opposite of what PHASE-1 was just built to guarantee.
   - **The one real point in its favor:** it needs no new, unverified Alpaca
     API surface — `adjustment="all"` on a full historical pull is the same
     mechanism `fetch_bars.py`/`fetch_history.py` already use successfully,
     whereas the corporate-actions endpoint is confirmed present in the SDK
     but not yet exercised live in this environment.
   - **Decision: keep per-symbol corporate-actions detection as the sole
     mechanism.** It is cheaper, reacts the same night rather than within a
     six-day window, and is the only one of the two consistent with PHASE-1's
     precedent. The unverified-API risk is real but is exactly what TASK-3.7
     already gates on before this phase is trusted in production — it is not
     waived by switching to a cruder mechanism preemptively (CLAUDE.md: no
     handling built for a failure not yet confirmed). **Named as the explicit
     fallback, not built now:** if TASK-3.7's live check shows the
     corporate-actions endpoint does not reliably surface known splits (the
     existing halt condition), a periodic full refetch of the long file — on
     a quarterly cadence, matching `PLAN-2026-09-09-daily-ingest.md`
     PHASE-4(a)'s already-accepted reasoning that D-95's bands are stable
     across a 4-year gap and need no more frequent recomputation — is the
     documented Plan B, not a weekly one, since a quarterly cadence is enough
     once detection is no longer providing the fast reaction and only needs
     to bound worst-case staleness, not eliminate it same-day.

**Residual gap, named rather than silently absorbed:** detection is scoped to
`forward_splits`, `reverse_splits` and `unit_splits` — the three types whose
ratios are large enough to plausibly reproduce the jump measured above.
`stock_dividends` and `spin_offs` also alter the adjusted basis and are not
checked here; their typical magnitude is far smaller than a split's, so far
less likely to corrupt a correlation, but this is an unmeasured assumption,
not a proven absence of risk — stated so it is not silently assumed away.

**Confirmed live, 2026-09-09 (team-lead, real credentials).**
`CorporateActionsRequest(symbols=["NVDA","AAPL"], start=2024-06-01,
end=2024-06-20)` against the real API returned exactly one entry, under a
`forward_splits` key: NVDA, `new_rate=10.0`, `old_rate=1.0`,
`ex_date=2024-06-10`, `process_date=2024-06-10` — precisely the boundary date
measured in the file, and well inside the ±5-day pad. AAPL returned nothing
for this window — correctly, since its own split (2020-08-31) is outside it —
which confirms the endpoint genuinely filters by symbol and date rather than
returning everything; **AAPL's own split, in its own window, has not yet been
queried and remains open** (TASK-3.7). Two properties of the real response,
not visible from the type stubs alone, and now load-bearing:
- **Absent keys, not empty lists.** The response had no `reverse_splits` or
  `unit_splits` key at all — not `[]`, absent entirely. `split_affected_
  symbols` reads every type via `.get(type_name, [])`, never a bare subscript
  (TASK-3.3/3.4 above are written against this).
- **The parsed dict lives at `.data`.** `CorporateActionsSet.data` is what
  `split_affected_symbols` is called with; this design never sets
  `raw_data=True`, so `.data` is always present and no `hasattr` fallback is
  needed (TASK-3.5 above is written against this).

**Is repeated re-detection of the same historical split possible, forever?**
No — bounded, and here is why, rather than an assertion (team-lead's own
correction of the initial framing, confirmed). The query window's lower
bound, `watermark - 5 days`, moves forward every night together with
`watermark` itself, which advances to within a day of "today" on every
successful run (both the affected-symbol refetch and the ordinary incremental
path extend the file through `today`). For a fixed historical `ex_date` D,
the window contains D only while `watermark <= D + 5`; since watermark
increases by roughly one session per successful run, D falls out of
`[watermark - 5, today]` permanently within roughly three to six nightly runs
of first being detected (the exact count depends on where a weekend falls
inside the 5-calendar-day pad), and is never queried again after that. The
cost of those few extra nights is one full refetch of one
already-correctly-adjusted symbol — harmless (`merge_bars`'s `keep="last"`
just replaces already-correct rows with byte-identical fresh ones) and
negligible against the ~2,183-symbol universe. **Do not build deduplication
for this** — suppressing a bounded, harmless repeat would itself be error
handling for a non-problem, the same CLAUDE.md rule this plan already
invoked against a weekly-full-refetch fallback. The only way this would leak
indefinitely is if `watermark` stopped advancing at all — i.e. the nightly
job is already broken in a different, already-visible way (every run
reporting `0 new rows` forever), which is a pre-existing failure class this
phase does not need to guard against a second time.
**Operator note, so this is not mistaken for a stuck job:** the ingest's
`done` line will therefore name the same symbol as "refetched for a split"
on a few consecutive nightly runs after a real split first occurs. This is
expected, self-limiting behaviour, not a hang — it stops on its own once the
window's lower bound passes `ex_date + 5 days`.

**Depends on:** nothing else in this plan (parquet only).

**Completion criterion:** `uv run pytest -q tests/test_ingest.py -q` all pass.
Operational (needs live Alpaca credentials): running
`scripts/fetch_daily_bars.py` twice back-to-back prints `0 new rows` and
`0 symbols refetched for a split` the second time; a manual
`CorporateActionsRequest` for `NVDA`, `2024-06-01`..`2024-06-15` returns
exactly one `forward_splits` entry — the direct check that this design's
load-bearing assumption about the endpoint holds, not merely that it is
plausible from reading the SDK's model classes.

The unit of incrementality is a trading-session date across the whole file,
not per-symbol (bars for one universe arrive on one calendar) — matching the
already-written rationale in `PLAN-2026-09-09-daily-ingest.md` PHASE-2, not
repeated here. The existing ~2,183-symbol column set is reused as-is; no
universe refresh (see "Open question: the universe gap").

- TASK-3.1 (test — **already done**, unaffected by this revision).
  `tests/test_ingest.py` exists and passes: `test_merge_bars_dedupes_by_
  symbol_and_timestamp`, `test_merge_bars_is_a_noop_on_identical_rerun`,
  `test_daily_watermark_is_the_max_timestamp_present`,
  `test_daily_watermark_on_empty_frame_returns_none`. Confirmed present by
  reading the file directly; no action needed.
- TASK-3.2 (impl — **already done**, unaffected by this revision).
  `src/lagmatrix/ingest.py` exists with `merge_bars`, `daily_watermark`
  exactly as specified. Confirmed present and passing; no action needed.
- TASK-3.3 (test, new in this revision — **fixture shape corrected after
  the first draft was found wrong against the real API**, see below). Add
  to `tests/test_ingest.py`, importing `split_affected_symbols` from
  `lagmatrix.ingest` (observe `ImportError` first).
  **Fixture shape, corrected:** the first draft of this task specified a
  dict-of-dicts fixture (`{"forward_splits": [{"symbol": "NVDA", ...}]}`)
  and `row["symbol"]` reads. Both are wrong — confirmed live:
  `CorporateActionsClient.get_corporate_actions` returns pydantic model
  instances, not dicts. `response.data` is `dict[str, list[ForwardSplit |
  ReverseSplit | UnitSplit | ...]]`; each item is read by **attribute**
  (`row.symbol`), never subscript (`row["symbol"]` raises `TypeError:
  'ForwardSplit' object is not subscriptable`, confirmed live). Fixtures
  therefore use `types.SimpleNamespace` as a lightweight attribute-access
  stand-in for the real model classes — a deliberate KISS choice, not an
  accident: the real `ForwardSplit`/`ReverseSplit`/`UnitSplit` pydantic
  models require filling in every required field (`cusip`, `process_date`,
  etc.) that `split_affected_symbols` never reads, and `SimpleNamespace`
  carries only the attributes the function actually touches.
  `split_affected_symbols` is written to take exactly what the API returns
  (model instances) rather than a caller-side dict conversion — a
  conversion step would itself need testing against the real shape, which
  is the exact untested seam that produced this correction.
  - `test_split_affected_symbols_finds_a_forward_split`:
    `{"forward_splits": [SimpleNamespace(symbol="NVDA")]}` → `{"NVDA"}`.
  - `test_split_affected_symbols_finds_a_reverse_and_unit_split`:
    `{"reverse_splits": [SimpleNamespace(symbol="XYZ")], "unit_splits":
    [SimpleNamespace(old_symbol="ABC", new_symbol="ABCD")]}` → `{"XYZ",
    "ABC"}`. **`UnitSplit` has no `symbol` field at all** — confirmed
    directly against the installed `alpaca.data.models.corporate_
    actions.UnitSplit`, whose fields are `old_symbol`/`old_cusip`/
    `old_rate`/`new_symbol`/`new_cusip`/`new_rate`/`alternate_symbol`/
    `alternate_cusip`/`alternate_rate`/`process_date`/`effective_date`/
    `payable_date` — so this test pins `old_symbol` specifically, not a
    uniform `.symbol` read across all three types.
  - `test_split_affected_symbols_ignores_other_action_types`:
    `{"cash_dividends": [SimpleNamespace(symbol="Q")]}` → empty set — the
    explicit boundary of this plan's stated scope (dividends/spin-offs not
    covered, named above).
  - `test_split_affected_symbols_on_empty_response_is_empty`: `{}` → empty
    set, not an exception — the first-run/no-actions-found case.
  - `test_split_affected_symbols_ignores_a_missing_key_rather_than_erroring`:
    a fixture with only `{"forward_splits": [...]}` present — no
    `reverse_splits`/`unit_splits` key at all, not even an empty list — does
    not raise `KeyError`. Pinned because Alpaca's real response (confirmed
    live, below) omits a corporate-action type's key entirely when there are
    zero results for it, rather than including it as `[]`.
- TASK-3.4 (impl, new in this revision). `src/lagmatrix/ingest.py`: add
  `split_affected_symbols(actions_data: dict) -> set[str]`, reading each
  type via `actions_data.get(type_name, [])` -- never a bare subscript on
  the outer dict, since Alpaca omits a type's key entirely rather than
  returning `[]` for it (confirmed live). Each **item** in that list is a
  pydantic model instance, read by **attribute, never dict subscript**:
  `row.symbol` for `forward_splits`/`reverse_splits` entries, `row.
  old_symbol` for `unit_splits` entries (confirmed directly against the
  installed `alpaca.data.models.corporate_actions.UnitSplit`, which has no
  `symbol` field at all — see TASK-3.3's corrected fixture note). A unit
  split that also renames the symbol (`old_symbol != new_symbol`) is not
  otherwise handled by this plan — named as a residual risk, not solved,
  since a symbol rename is a distinct, larger problem than the
  adjustment-basis break this phase targets.
- TASK-3.5 (script exemption — I/O wiring calling already-tested pure
  functions, matching this repo's own precedent for every other
  bars-fetching script). `scripts/fetch_daily_bars.py`: read
  `data/bars-10y.parquet` if present (else an empty frame); compute
  `watermark` via `daily_watermark`. If `watermark` is not `None`, query
  `CorporateActionsClient.get_corporate_actions` for the file's existing
  symbol columns, `start=watermark - timedelta(days=5)`, `end=today`,
  `types=["forward_split", "reverse_split", "unit_split"]`; compute
  `affected = split_affected_symbols(response.data)` -- `.data` is the
  attribute confirmed by a live probe against the real API (see below);
  `CorporateActionsClient` is never constructed with `raw_data=True` in this
  design, so `.data` is always present and no `hasattr` fallback is needed.
  For symbols in
  `affected`, fetch each one's **full** history (`start` = the file's own
  earliest stored date for that symbol, `end=today`) fresh. For every other
  symbol, fetch only `watermark + 1 day` through `today` (the original,
  unaffected incremental path — skip the API call and print `0 new rows` if
  already current). Concatenate both fetches into one `new` frame and call
  `merge_bars(existing, new)` **unchanged** — `keep="last"` already resolves
  an affected symbol's stale rows in favor of the fresh, fully refetched ones
  wherever timestamps coincide (traced directly against
  `drop_duplicates(subset=["symbol","timestamp"], keep="last")`: the fresh
  refetch is concatenated after `existing`, so it wins on every overlapping
  date), so **no separate replace function is needed**. Print, and include
  in the script's own summary line, which symbols (if any) were refetched
  for a detected split — visible, not hidden. Write back via
  temp-file-then-rename, as originally specified. Add `"fetch_daily_bars"`
  to `tests/test_scripts_import.py`'s `SCRIPTS` parametrize list
  (`tests/test_scripts_import.py:33`).
- TASK-3.6 (verify). `uv run pytest -q` — count increases by exactly 5 over
  the running total at the point TASK-3.1/3.2 already landed (this revision's
  5 new tests in TASK-3.3; TASK-3.1/3.2's own tests are already counted).
- TASK-3.7 (verify, operational — needs live Alpaca credentials).
  **NVDA half already confirmed** (see the live-evidence note above) — no
  action needed for it. **Still open:** query `AAPL`, `2020-08-26`..
  `2020-09-05` (its own 2020-08-31 4:1 split, not yet queried against its own
  window — team-lead's probe used NVDA's window for both symbols, so AAPL
  returning nothing there is not evidence about AAPL's own split) and confirm
  a `forward_splits` entry is returned — a single symbol matching is not
  proof the mechanism generalises to a different split, a different date and
  a different rate. Also run `scripts/fetch_daily_bars.py` twice; confirm
  `0 new rows` and `0 symbols refetched` on the second call, and that
  `data/bars-10y.parquet`'s last session advanced to at least yesterday's
  close on the first.

**What would make this wrong:** if a split's `ex_date`/`effective_date` is
reported by Alpaca more than 5 calendar days after the day it actually affects
the adjustment basis of daily bars, the stated pad would miss it — TASK-3.7's
live NVDA check is the guard: if the known 2024-06-10 split is not found by a
query spanning it ±5 days, the pad is wrong and needs widening before this
phase is trusted on symbols whose split date is not already known. Separately,
if `get_corporate_actions` rejects a request for ~2,183 symbols in one call (a
request-size limit neither confirmed nor ruled out by reading the SDK),
TASK-3.5 needs batching the same way `fetch_bars.py`/`fetch_history.py`
already batch bars requests at `BATCH=200` — check this during TASK-3.7, not
assumed clean. Unrelated to this revision, unchanged from the original draft:
Alpaca revising a previously-reported close after the watermark has advanced
past that date is not caught by anything in this phase (`merge_bars`'s
`keep="last"` only helps within one run's own fetched range) — named, not
silently accepted. Separately: if a real run's peak memory is materially
larger than "read the existing file once, refetch a handful of split-affected
symbols in full plus new sessions for everyone else, write once", stop before
wiring this into any CronJob resource limit `homelab-deploy` PHASE-5 sets,
since that limit was sized without this script existing.

---

## PHASE-4 — `as_of` is derived after the long file is extended, never before

**Depends on:** PHASE-1 (loud failure helper), PHASE-2 (`default_as_of()`
agrees with `COMOVE_CLOSES()` by construction — done, D-109), PHASE-3
(`fetch_daily_bars.py` exists to be wired in).

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
  writing (already past D-109 as of this revision — do not hardcode a
  number). Via `spike-log`, append one decision covering: the long file was
  never extended and `as_of` was derived from the wrong file before the fetch
  ran (partly already logged as D-109); the fix (extend `bars-10y.parquet`
  incrementally with a corporate-actions split guard, resolve `as_of` after
  extension, fail loudly on insufficient history); and the decision to keep
  the two bars files separate rather than merge them.
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
- If PHASE-3's real-Alpaca run (TASK-3.7) shows `scripts/fetch_daily_bars.py`
  needing to change `data/bars-10y.parquet`'s symbol columns — a new IPO's
  bars arriving, or a delisted symbol Alpaca no longer serves — stop. That is
  the universe-refresh question this plan explicitly does not answer, and
  the fix is not "merge whatever columns show up."
- NVDA's known 2024-06-10 split is confirmed found within the ±5-day
  window (live, team-lead). If TASK-3.7's still-open AAPL check does not
  find its own 2020-08-31 split within the same ±5-calendar-day window, stop
  PHASE-3 and widen the pad (with a new justification, not a re-tuned guess)
  before wiring `fetch_daily_bars.py` into the nightly graph — do not ship a
  split guard confirmed on only one of the two known cases.
- If TASK-4.3 cannot demonstrate `as_of` changing between "before" and
  "after" `node_long_bars` runs without also changing `serve.default_as_of`'s
  real implementation in a way PHASE-2/D-109 did not anticipate, stop — it
  means PHASE-2 and PHASE-4 disagree about what `default_as_of()` returns
  and that needs resolving before either is called done.
- If fixing `default_as_of()` (PHASE-2, already done) changed a
  previously-published number for a date **both** files claim to have (not
  merely extending which dates are answerable), stop — that would mean the
  two files disagree about a session they both cover, which is a data
  correctness problem, not an ordering one, and is out of this plan's scope
  to fix blind.
