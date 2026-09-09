# PLAN-2026-09-09-daily-ingest

**Goal:** Give the project a daily, idempotent ingest pipeline for new bars and
new news, and a new co-movement graph in ArangoDB — built and orchestrated so
that running it twice is a no-op and a crashed run resumes rather than
restarting.

**Decisions this depends on:** D-97 (the loaders drop collections; nothing may
run daily until they upsert — this plan's PHASE-1 exists solely to satisfy
this), D-95 (contemporaneous co-movement replicates out of sample, corr=0.640,
1,236,371 pairs, the calibrated band table this plan's PHASE-4 makes
operational), D-93/D-94 (no lagged pairwise or chained structure survives out
of sample — the reason this plan's graph carries no `lag_days` and no
prediction claim), D-96 (a shocked symbol's own next move is a coin
flip — cited for what the graph must not imply, not built by this plan), D-90
(the shipped pipeline is a contemporaneous co-movement detector with a
supply-chain overlay, not a lead-lag engine), D-87 (`room` was deleted for
being a denominator/confidence number attached to an unmeasured claim — the
precedent this plan's storage threshold in PHASE-4 exists to avoid repeating),
D-35 (which LangGraph primitives this project has already adopted, and which
it declined, for reasons this plan reuses rather than re-litigates), D-45
(`AsyncSqliteSaver` is the checkpointer of record), D-16 (Alpaca is the sole
market/news data source), D-13/D-02 (ArangoDB is the one store for both the
market topology and the vectors), D-38 (a silent path gets an explicit
outcome, not a plausible default — applied twice below: the missing-watermark
case and the below-threshold-correlation case).

Related but out of scope: `docs/plans/PLAN-2026-09-09-leader-in.md` (D-90's
descriptive symbol-in feature, built on the *supply* graph). This plan adds a
second, independent edge type — measured co-movement — to the same ArangoDB
database. It does not touch `leader-in`'s work, and does not wire the new
`moves_with` collection into the live `graph_retriever.py` node, which still
computes its own in-memory correlation per D-34's original reasoning ("no
ArangoDB yet" — no longer true, but re-plumbing the live retrieval path is a
separate decision this plan does not make; see "Out of scope" below).

**Open questions that could invalidate it:** none block starting. Q-43 (the
live-Arango tests default correctly now, and `LAGMATRIX_REQUIRE_LIVE=1` turns
an unreachable instance into a failure — every live-gated test this plan adds
follows that same fixture) is background, not a blocker. If Q-45 (deepening
the supply graph) ever lands, it has no effect on this plan — co-movement
edges are computed from price history alone and do not depend on filing
coverage.

## What this does not claim

Every number this plan writes to ArangoDB is a measured statement about how
reliably two names have moved **together**, in the same session. None of it
is a statement about which one moves first, or about what either one does
*next*.

- No edge carries `lag_days` with any value other than the literal
  contemporaneous meaning `0` already used in `graph_retriever.py:61` — never
  a forecast horizon.
- No field, method or UI string introduced by this plan may use the words
  "predicts", "leads", "signals" or "forecasts" for a co-movement edge. The
  correlation collection is named `moves_with`, not `leads_to`.
- The per-pair number stored is a **calibrated expectation of same-day
  co-movement strength**, drawn from D-95's out-of-sample band table — not the
  raw same-window correlation, which D-95 itself shows is optimistic in
  proportion to how it was selected.
- D-96's finding (a shocked symbol's own move is a coin flip after the fact)
  is cited here as a **constraint on interpretation** — a reader must not
  combine "A and B move together" with "A just moved" and conclude "B is about
  to move." Nothing in this plan computes or stores that combination.
- Below the measured floor (`|corr| < 0.3`, D-95's lowest calibrated band) no
  edge is written at all, rather than writing one with a guessed or
  extrapolated interval. This is the same discipline D-87 applied when it
  deleted `room` for being a confidence number attached to something never
  actually measured.

## Success criteria

1. `uv run pytest -q` — full suite green, no reduction in count from the 128
   passing today (new tests only add to it; nothing is deleted).
2. `uv run python scripts/check_baseline.py --synthetic` prints
   `baseline unchanged: 6 rows identical` after every phase below.
3. `grep -n '_drop' scripts/load_arango.py scripts/load_vectors.py` finds no
   occurrence naming `equity`, `supplies_to`, `co_mentioned` or `article`.
4. Running `scripts/run_daily_ingest.py` twice in immediate succession against
   the same day's data produces zero new bar rows, zero new edges, and an
   unchanged `co_mentioned`/`supplies_to`/`equity`/`article`/`moves_with`
   document count on the second run.
5. A crashed run of `scripts/run_daily_ingest.py` (killed mid-batch, same
   `thread_id`) resumes rather than restarting bars fetch from batch 1 —
   demonstrated in `tests/test_ingest_graph.py`, not merely asserted.
6. A real (not synthetic) run of `scripts/calibrate_comovement.py` against
   `data/bars-10y.parquet` reproduces D-95's headline number,
   `corr(discovery, validation) ≈ 0.640`, and flags the same same-company
   screen population size (7 pairs at `|corr| >= 0.95` on the same data) —
   proof the productionised script is the spike's math, not a rewrite of it.

## Naming and scope of the new pieces

| New thing | Where | Kind |
|---|---|---|
| `merge_bars`, `daily_watermark` | `src/lagmatrix/ingest.py` | pure, tested |
| `filter_since`, `next_watermark` | `src/lagmatrix/ingest.py` | pure, tested |
| `market_excess`, `pct_identical_returns`, `classify_pair_artifact`, `band_for`, `build_comovement_edges` | `src/lagmatrix/comovement.py` | pure, tested |
| `scripts/fetch_daily_bars.py` | new script | I/O wiring, exempt |
| `scripts/calibrate_comovement.py` | new script | I/O wiring, exempt |
| `scripts/run_daily_ingest.py` | new script | I/O wiring, exempt |
| `src/lagmatrix/graph/ingest_builder.py` | new module | LangGraph wiring, exempt (smoke-tested) |
| ArangoDB `moves_with` (edge) | production `lagmatrix` DB | new collection, written by PHASE-4/5 |
| ArangoDB `ingest_meta` (document) | production `lagmatrix` DB | new collection, holds the vectors watermark |
| `data/comovement-bands.csv`, `data/comovement-exclusions.csv`, `data/comovement-share-classes.csv` | committed | calibration artifacts, same category as `data/excluded-etfs.csv` |

---

## PHASE-1 — Loaders upsert instead of dropping (D-97 gate)

**Blocks:** every other phase that touches ArangoDB (PHASE-3, PHASE-4,
PHASE-5). PHASE-2 (bars, which lives in parquet, not ArangoDB) does not depend
on this phase and could ship first.

**Completion criterion:** `uv run pytest tests/test_loader_idempotency.py -q`
all pass, plus `grep -n '_drop' scripts/load_arango.py scripts/load_vectors.py`
shows no line naming `equity`, `supplies_to`, `co_mentioned` or `article`.

The transport constraint, stated once so it does not need repeating per task:
`load_arango.py`/`load_vectors.py` reach ArangoDB only via
`ssh -> kubectl exec -> arangosh`, because the dev/CI box has no direct route
to the homelab cluster except a manual tunnel. That SSH glue is not
exercisable from here and this plan does not try to test it — the same
accepted gap Q-43 already records for these two scripts. What *is* testable,
and is where the actual correctness bug lives, is (a) the JS text that ships
to `arangosh`, and (b) the document-shaping functions that build what gets
inserted. Both are extracted into named, importable pieces below and tested
directly, with one live-gated test proving the upsert *strategy* (deterministic
key + `overwriteMode`) against a real disposable database.

- TASK-1.1 (test) Write `tests/test_loader_idempotency.py`. Import
  `ensure_collections_js` and `supply_edge_docs`/`comention_edge_docs` from
  `scripts/load_arango.py`, and `ensure_article_js` from
  `scripts/load_vectors.py` — none exist yet, so this fails on import
  (`ImportError`). Observe that failure before writing TASK-1.2.
  - `test_ensure_collections_js_never_drops`: `"_drop" not in
    ensure_collections_js({"equity": 2, "supplies_to": 3, "co_mentioned": 3})`.
  - `test_ensure_article_js_never_drops`: same assertion on
    `ensure_article_js()`.
  - `test_supply_edge_docs_have_deterministic_keys`: build a small list of
    `filing_mention`-shaped rows, call `supply_edge_docs(rows)` twice, assert
    the two `_key` lists are identical (byte-for-byte) — proves a second run
    upserts rather than inserting a duplicate edge, which is the failure mode
    `bulk()`'s current auto-generated-key edges have today.
  - `test_supply_edge_docs_dedupes_a_repeated_pair`: two input rows for the
    same `(supplier, customer)` (e.g. two filings restating the same
    relationship) collapse to one output doc keyed
    `f"{supplier}->{customer}"`.
  - `test_comention_edge_docs_have_deterministic_keys`: mirror, keyed
    `f"{a}~{b}"` (the row's own `a`/`b` order, not re-sorted — the upstream
    query already fixes an order per row and re-sorting here would only add a
    branch nothing depends on).
  - **Negative control, required before calling this green:** temporarily
    reinsert `if (db._collection("article")) { db._drop("article"); }` into
    `ensure_article_js()`'s returned string and confirm
    `test_ensure_article_js_never_drops` fails. This is the falsifiable check
    the task requires — a test that cannot fail proves nothing.
- TASK-1.2 (impl) In `scripts/load_arango.py`: extract the collection-creation
  block into `ensure_collections_js(want: dict[str, int]) -> str`, replacing
  the `if (db._collection(name)) { db._drop(name); }` loop with
  `if (!db._collection(name)) { db._create(name, {}, want[name] === 3 ?
  "edge" : "document"); }`. Extract `supply_edge_docs(rows)` /
  `comention_edge_docs(rows)` as pure functions returning the doc dicts
  `bulk()` already builds inline, adding `"_key"` to each. Update `main()` to
  call these three instead of the inline blocks it has today; `bulk()`'s
  `overwriteMode:"replace"` (unchanged) now upserts by the new deterministic
  key instead of relying on `_key` being absent.
  In `scripts/load_vectors.py`: extract `ensure_article_js() -> str` with the
  create-if-absent form; `article` documents already key on `str(row.id)`
  (stable), so no change needed there.
- TASK-1.3 (test) Write one live-gated integration test in the same file,
  `test_upsert_strategy_is_idempotent_against_a_real_database`, using
  `arango_db_or_skip("test_loader_idempotency")` (the existing fixture,
  `tests/conftest.py`). Seed one pre-existing, unrelated document in a
  throwaway `equity` collection; call `collection.insert(doc,
  overwrite_mode="update")` twice with the same `_key` for a second document;
  assert (a) the seeded document is still present and unchanged (proves
  nothing was dropped), and (b) the collection's count is 2, not 3, after the
  second insert (proves the upsert, not the drop, is what makes reruns safe).
  This validates the strategy against a real server; it does not and cannot
  validate the SSH transport itself, which is out of reach from here — stated
  as an accepted limitation, not hidden.
- TASK-1.4 (impl) None — TASK-1.3 exercises the strategy directly via
  `python-arango`, which is what TASK-1.2's `bulk()` already does through
  `arangosh`'s equivalent JS call. No production code changes beyond TASK-1.2.
- TASK-1.5 (script exemption) Add `"load_arango"`, `"load_vectors"` and
  `"load_news"` to the `SCRIPTS` parametrize list in
  `tests/test_scripts_import.py`. None of the three loaders are imported by
  anything under `tests/` today — the same blind spot Q-43 found for
  `serve.py`/`capture_showcase.py`, and this plan is editing exactly the two
  scripts that blind spot would hide a break in. Script-category, no TDD:
  this only proves the module imports cleanly.

**What would make this wrong:** if `overwriteMode:"replace"` on an edge
silently drops fields not present in the new doc (ArangoDB's `"replace"` mode
does exactly that, by design — a partial doc replaces the whole document). If
a future filing correction needs to *clear* a field (e.g. `pct_revenue` is
retracted), `"replace"` with a full doc is correct as designed here since
`supply_edge_docs` always emits the complete row; it would be wrong only if a
caller ever passed a partial doc, which no code path in this plan does.

---

## PHASE-2 — Daily bars ingestion

**Depends on:** nothing in this plan (parquet only, no ArangoDB).

**Completion criterion:** `uv run pytest tests/test_ingest_bars.py -q` all
pass. Operational verification (not part of the automated suite, needs live
Alpaca credentials): running `scripts/fetch_daily_bars.py` twice back-to-back
produces `0 new rows` on the second run, printed by the script itself.

The unit of incrementality is a **trading session date, per the whole bars
file** (not per symbol): `data/bars-10y.parquet`'s existing 1,573-symbol,
90%-coverage universe (D-95's own population) is fetched as one wide batch per
`fetch_bars.py`'s existing `BATCH=200`-chunking pattern, and the watermark is
the single max timestamp already present across the whole file. Per-symbol
watermarks (as `load_news.py` uses) are not needed here: bars for the whole
universe arrive on the same trading calendar, so a single watermark is
sufficient and simpler — CLAUDE.md's "no configurability that wasn't
requested" argues against building per-symbol tracking nothing asks for.

`data/bars.parquet` and `data/bars-5y.parquet` are **not** touched by this
plan — they are fixed inputs to `check_baseline.py`'s non-synthetic path and
`fetch_history.py`'s one-off pulls; silently growing them would break
reproducibility of numbers already recorded against their current contents
(the same lesson Q-27 already recorded once). `data/bars-10y.parquet` is the
file this plan grows daily, since it is already the widest committed-in-spirit
universe and the one D-93/94/95/96 and this plan's PHASE-4 are built against.

Universe refresh (adding newly-liquid symbols, dropping delisted ones) is
explicitly **out of scope** — the existing 1,573-symbol column set is reused
as-is. Flagged as an open question below, not silently handled.

- TASK-2.1 (test) Write `tests/test_ingest_bars.py`, importing `merge_bars`
  and `daily_watermark` from `src/lagmatrix/ingest.py` (does not exist yet —
  observe the `ImportError` before implementing).
  - `test_merge_bars_dedupes_by_symbol_and_timestamp`: an "existing" frame and
    a "new" frame share one `(symbol, timestamp)` row with a different
    `close`; assert the merged frame has one row for that key and it carries
    the new frame's value (the fresher fetch wins, matching Alpaca's own
    late-arriving-correction behaviour).
  - `test_merge_bars_is_a_noop_on_identical_rerun`: `merge_bars(df, df)` has
    the same row count and the same values as `df` — the literal idempotency
    requirement from the top-level success criteria.
  - `test_daily_watermark_is_the_max_timestamp_present`: trivial, but pins the
    contract `fetch_daily_bars.py` builds its `--since` from.
  - `test_daily_watermark_on_empty_frame_returns_none`: an empty DataFrame
    (the very first run, before any parquet exists) returns `None`, not
    `NaT` and not a raised exception — the explicit outcome D-38 requires for
    a case that would otherwise fail silently or confusingly deep inside
    `fetch_daily_bars.py`'s date arithmetic.
- TASK-2.2 (impl) `src/lagmatrix/ingest.py`:
  `merge_bars(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame`
  (`pd.concat` + `drop_duplicates(subset=["symbol", "timestamp"],
  keep="last")` + sort); `daily_watermark(df: pd.DataFrame) -> date | None`
  (`None` on an empty frame, else `df.timestamp.max().date()`).
- TASK-2.3 (script exemption) `scripts/fetch_daily_bars.py`: read
  `data/bars-10y.parquet` if present (else empty frame), compute the
  watermark, fetch Alpaca daily bars for the existing universe from
  `watermark + 1 day` through today (skip the API call entirely and print
  `0 new rows` if the watermark is already the last completed session — the
  no-op path for a same-day rerun or a weekend/holiday), merge, and write back
  via a temp-file-then-rename (never an in-place overwrite) so a crash mid-write
  leaves the previous file untouched — the resume mechanism for this phase.
  Add `"fetch_daily_bars"` to `tests/test_scripts_import.py`'s `SCRIPTS` list.

**What would make this wrong:** Alpaca revising a previously-reported close
(a late corporate-action adjustment) after the watermark has already advanced
past that date — `merge_bars`'s `keep="last"` only helps within one run's
overlap; a correction arriving *after* the watermark has moved on would need a
periodic full re-pull to be caught, which this plan does not build and states
as an open question rather than a silent gap.

---

## PHASE-3 — Daily news and vector ingestion, incremental

**Depends on:** PHASE-1 (vectors write to ArangoDB's `article` collection and
a new `ingest_meta` collection).

**Completion criterion:** `uv run pytest tests/test_vector_watermark.py -q`
all pass.

`load_news.py` needs no changes — D-97 already names it the model: per-symbol,
per-window coverage in Postgres, `ON CONFLICT DO NOTHING`. It is called
unchanged as part of the nightly job (PHASE-5).

`load_vectors.py`'s unit of incrementality becomes a **watermark on the
source article's `created_at` date**, stored in ArangoDB's new `ingest_meta`
collection (`_key: "vectors_watermark"`, one field: `last_date`) — the
ArangoDB-native equivalent of `news_coverage`, owned by the same script that
already owns the `article` collection. (Named `ingest_meta`, not `_meta`:
ArangoDB reserves collection names starting with `_` for system collections.)
Resume granularity is one embedding chunk (`CHUNK=400` articles, unchanged):
the watermark advances only after a chunk's docs are successfully written, so
a crash mid-run re-embeds at most one chunk's worth of articles on restart —
wasteful by at most `CHUNK` rows, never destructive, and never re-embedding
the whole corpus, which is the cost D-97 flagged as unacceptable at a nightly
cadence.

- TASK-3.1 (test) Write `tests/test_vector_watermark.py`, importing
  `filter_since` and `next_watermark` from `src/lagmatrix/ingest.py` (observe
  `ImportError` first).
  - `test_filter_since_keeps_only_rows_after_the_watermark`: a small DataFrame
    with a `date` column, some rows before and some after a given watermark;
    assert only the later ones survive, and the boundary date itself is
    excluded (strictly after, matching `load_news.py`'s own window semantics
    of non-overlapping coverage).
  - `test_filter_since_with_no_watermark_keeps_everything`: `watermark=None`
    (first-ever run) returns the input unchanged — the explicit first-run
    outcome, not a crash on `None` comparison.
  - `test_next_watermark_advances_to_the_batch_max`: `next_watermark(current,
    batch_max)` returns `batch_max` when it is later than `current`, and
    `current` unchanged if a batch (out of order, defensively) is older —
    the watermark must never move backwards.
- TASK-3.2 (impl) `src/lagmatrix/ingest.py`: add `filter_since(df, watermark:
  date | None, column: str = "date") -> pd.DataFrame` and `next_watermark(
  current: date | None, batch_max: date) -> date`.
- TASK-3.3 (script exemption) `scripts/load_vectors.py`: replace the
  `--since` argparse default with a read of `ingest_meta/vectors_watermark`
  (falling back to the existing `2025-01-01` default only when the document is
  absent — the same first-run case TASK-3.1 already covers). After each
  `CHUNK`-sized batch is written, advance the watermark via `next_watermark`
  and write it back. Filter the fetched DataFrame with `filter_since` before
  embedding, so articles already embedded are never re-embedded.
  `tests/test_scripts_import.py` already gained `"load_vectors"` in PHASE-1.

**What would make this wrong:** an article whose `created_at` is
back-corrected by the source to a date before the current watermark would
never be picked up — the same class of gap as PHASE-2's late-arriving Alpaca
correction, and, like that one, named here rather than silently accepted.

---

## PHASE-4 — Co-movement graph construction (the new piece)

**Depends on:** PHASE-1 (writes to ArangoDB) and PHASE-2 (reads
`data/bars-10y.parquet`, which must be current for the numbers to mean
anything).

**Completion criterion:** `uv run pytest tests/test_comovement.py -q` all
pass. Real-data verification (documented, not automated —
`data/bars-10y.parquet` is untracked): `uv run python
scripts/calibrate_comovement.py` reproduces `corr(discovery, validation) ≈
0.640` and flags 7 pairs at the `|corr| >= 0.95` screen, matching D-95.

This phase has two distinct pieces, deliberately not one:

**(a) Calibration — periodic, not nightly.** `scripts/calibrate_comovement.py`
re-runs D-95's own discovery/validation-split methodology (reusing
`scripts/experiment_lag_matrix.py`'s market-excess-then-split pattern, at lag
0 only) against `data/bars-10y.parquet`, and writes three small, committed
artifacts: `data/comovement-bands.csv` (columns: `band_lo`, `band_hi`,
`val_mean`, `val_sd`, `sign_hold_rate` — the rows of D-95's own band table),
`data/comovement-exclusions.csv` (pair, reason — the artefact-screened pairs,
e.g. NATL/LINE), and `data/comovement-share-classes.csv` (pair — the
legitimate same-company pairs the user said to keep, e.g. GOOGL/GOOG). Run
by hand or on a slow cadence (this plan proposes quarterly — D-95 found the
bands stable across a 4-year discovery/validation gap, so there is no reason
to re-derive them nightly); **not** a node in the nightly LangGraph job.
Script-category, no TDD, but see TASK-4.1 for what *is* unit-tested (the
screen it depends on).

**(b) Nightly edge write.** Cheap and mechanical: compute today's trailing
market-excess correlation for every pair not already excluded, look up which
committed band it falls in, and upsert a `moves_with` edge carrying that
band's calibrated numbers — never a number recomputed from a live
validation split, because there is no live validation split at 3am on a
Tuesday. This is what "recompute all, or incremental?" resolves to:
**correlation values recompute in full every night** (cheap — one `n × n`
matrix on `n=1,573`, sub-second), but **calibration recomputes rarely and
explicitly** (piece (a)). Edge history is not separately retained: the bars
parquet already is the append-only history (PHASE-2), so recalibration always
has everything it needs by re-running (a) against the grown file — no second
history mechanism is built.

**Same-company artefact vs. genuine share class — the screen, stated in
full.** For every pair at `|discovery corr| >= 0.95`:
- `pct_identical_returns` — the fraction of sessions where the two symbols'
  returns are bit-identical. NATL/LINE's measured 69.5% is the signature of
  one price feed duplicated under two tickers, not two companies moving
  together. **Assumption, stated because D-95 did not measure it and it
  matters if wrong:** genuine dual-class shares (GOOGL/GOOG) are assumed to
  have materially *lower* `pct_identical_returns` than a duplicated feed,
  since different share counts and rounding make daily percentage returns
  close but rarely bit-identical. This is checked, not merely assumed, the
  first time `calibrate_comovement.py` runs for real (TASK-4.3's real-data
  verification) — if GOOGL/GOOG's measured `pct_identical_returns` is not
  clearly separated from NATL/LINE's, the threshold in
  `classify_pair_artifact` needs revisiting before this phase is called done.
- Sign or magnitude collapse in the validation window (`sign(val corr) !=
  sign(disc corr)`, or `|val corr| < 0.5` while `|disc corr| >= 0.95`) is the
  second, independent check — NATL/LINE's own number (+1.000 → −0.325) fails
  this outright.
- A pair failing *either* check is excluded (`comovement-exclusions.csv`) and
  logged with which check it failed — D-38's explicit outcome, not a silent
  drop.
- A pair passing both is a genuine same-company pair
  (`comovement-share-classes.csv`), stored with `relation:
  "same_company_share_class"`, `expected_corr = trailing_corr`,
  `expected_corr_sd = 0.0`, `calibration_source: "structural"` — explicitly
  **not** run through D-95's statistical band table, because a dual share
  class tracks near-identically for structural reasons (same company, same
  cash flows), not because of the same statistical process the band table
  calibrates. Applying a shrinkage interval meant for one phenomenon to a
  different one would be a second, subtler version of the mistake this
  section exists to avoid.

**Storage threshold: `|trailing corr| >= 0.3`.** This is D-95's own measured
floor — the band table's lowest row is `0.3-0.40`; below it, D-95 measured
nothing, so no honest confidence interval exists to attach. Writing an edge
below this floor would repeat D-87's mistake in a new place: a number that
looks like a calibrated confidence interval but describes territory nobody
measured. At 1,573 symbols (~1.24M unordered pairs), this floor is expected
to keep the collection in the low hundreds of thousands (D-95's summed band
counts above 0.3 total 174,357) — small for ArangoDB, and every row in it
carries a number this project actually measured.

**Edge schema, `moves_with`** (edge collection, `equity -> equity`, one
undirected edge per unordered pair, `_key = f"{a}|{b}"` with `a < b`
lexicographically — correlation is symmetric, so a directed pair would only
double-store the same fact, which is exactly the kind of correlated-evidence
double-count Q-12/Q-40 already flag as a hazard elsewhere in this project):

```
_key            "AAPL|MSFT"
_from / _to     equity/AAPL, equity/MSFT
relation        "co_moves_with" | "same_company_share_class"
trailing_corr   float   -- today's measured market-excess correlation
corr_band       str     -- e.g. "0.6-0.7", or "structural" for a share class
expected_corr        float   -- the band's calibrated validation mean
expected_corr_sd     float   -- the band's calibrated validation sd
sign_stability       float   -- the band's measured sign-hold rate
calibration_as_of    date    -- when comovement-bands.csv was last generated
computed_at          datetime -- when this edge's trailing_corr was last written
window_start/window_end  date -- the trailing window trailing_corr was measured over
```

No `lag_days`, no `beta` — deliberately not reusing `LagEdge`'s fields (Q-39
already found `beta` overloaded between an accounting ratio and a volatility
ratio; adding a third meaning to it here would make that worse, not better).
This is a new, separate edge collection and does not touch `domain.models.
LagEdge` or `ArangoTopology`.

- TASK-4.1 (test) Write `tests/test_comovement.py`, importing `market_excess`,
  `pct_identical_returns`, `classify_pair_artifact`, `band_for` and
  `build_comovement_edges` from `src/lagmatrix/comovement.py` (observe
  `ImportError` first).
  - `test_market_excess_subtracts_the_cross_sectional_mean`: a tiny synthetic
    returns frame; assert each row sums to (approximately) zero after the
    transform.
  - `test_pct_identical_returns_on_a_duplicated_series_is_high`: two identical
    columns → `1.0`; two independent random columns → a low value (bounded
    check, not exact, since it is a random fixture — assert `< 0.1` with a
    fixed seed).
  - `test_classify_pair_artifact_flags_the_natl_line_signature`: `disc_corr =
    1.000, pct_identical = 0.695, val_corr = -0.325` → `True` (artefact).
  - `test_classify_pair_artifact_passes_a_genuine_share_class`: `disc_corr =
    0.97, pct_identical = 0.05, val_corr = 0.93` → `False`.
  - `test_band_for_finds_the_containing_row`: a small fixture band table
    (mirroring the D-95 rows); `band_for(0.65, bands)` returns the `0.6-0.7`
    row.
  - `test_band_for_below_the_floor_returns_none`: `band_for(0.1, bands)` →
    `None`, not the lowest row and not a raised exception — the explicit
    outcome for "measured nothing here," which `build_comovement_edges` must
    then skip rather than write with a fabricated interval.
  - `test_build_comovement_edges_keys_are_order_independent`: feeding the pair
    `(MSFT, AAPL)` and `(AAPL, MSFT)` through separately produces the same
    `_key`, `"AAPL|MSFT"` — proves the write is idempotent regardless of which
    order the correlation matrix iterates a pair in.
  - `test_build_comovement_edges_excludes_below_threshold`: a pair at
    `trailing_corr = 0.2` produces no edge at all.
- TASK-4.2 (impl) `src/lagmatrix/comovement.py`: implement the five functions
  above. `market_excess` is `returns.sub(returns.mean(axis=1), axis=0)` — no
  z-scoring, unlike `experiment_lag_matrix.py`'s `standardise`: Pearson
  correlation is invariant to per-column scaling, so the z-score step changes
  nothing about the correlation value and is dropped here as an unnecessary
  step (TASK-4.3's real-data reproduction of D-95's 0.640 figure is the check
  that this simplification is actually inert, not merely assumed to be).
- TASK-4.3 (script exemption) `scripts/calibrate_comovement.py`: the
  discovery/validation split (copy the split logic from
  `experiment_lag_matrix.py`, lag-0 only), producing the three CSVs. Add
  `!data/comovement-bands.csv`, `!data/comovement-exclusions.csv`,
  `!data/comovement-share-classes.csv` to `.gitignore`, immediately after the
  existing `!data/excluded-etfs.csv` line and its comment — same
  justification (a derived, symbol-level artifact carrying no raw price
  content, needed for anyone else's checkout to reproduce the calibration).
  Commit the three files once generated. Add `"calibrate_comovement"` to
  `tests/test_scripts_import.py`'s `SCRIPTS` list.
- TASK-4.4 (test) One live-gated integration test,
  `test_moves_with_upsert_is_idempotent`, in `tests/test_comovement.py`, using
  `arango_db_or_skip`: build two edges via `build_comovement_edges`, insert
  them into a throwaway `moves_with` collection twice, assert the count is 2
  (not 4) after the second insert and the field values are unchanged — the
  same pattern as PHASE-1's TASK-1.3, applied to this new collection.
- TASK-4.5 (script exemption) A nightly-write function,
  `write_comovement_edges(db, closes, bands, exclusions, share_classes) ->
  int` in `src/lagmatrix/comovement.py` (thin — reads today's window from
  `closes`, calls `market_excess`, computes the correlation matrix, calls
  `build_comovement_edges`, upserts via `python-arango`, returns the edge
  count written), which PHASE-5's `build_comovement_graph_node` calls
  directly. This is the one piece of PHASE-4 that talks to a live database in
  production, and it goes through `python-arango`, not the SSH/`arangosh`
  route the other two loaders use — ArangoDB is directly reachable over the
  existing tunnel for this new collection, same as `ArangoTopology` already
  does, so there is no transport constraint to work around here the way
  PHASE-1 had to.

**What would make this wrong:** if `pct_identical_returns` does not in fact
separate NATL/LINE-style artefacts from genuine share classes on real data
(the stated assumption above) — the fix would be a different discriminator
(e.g. comparing the two symbols' raw price *levels* for a fixed ratio, which a
true duplicate feed would also show), not lowering the threshold until the
known cases pass, which would be tuning a screen to its own test set.

---

## PHASE-5 — LangGraph orchestration of the nightly job

**Depends on:** PHASE-1 through PHASE-4 all individually correct — this phase
composes them and adds no new computation of its own.

**Completion criterion:** `uv run pytest tests/test_ingest_graph.py -q` all
pass, including a simulated crash-and-resume.

**Exemption:** infrastructure wiring, no behavioural change (CLAUDE.md's
stated exemption) — every node below calls an already-tested pure function or
an already-correct script entry point; the graph changes only how they are
sequenced, retried and checkpointed. Verified with an integration smoke test
in the style of `tests/test_fanout.py` and `tests/test_checkpointing.py`
(the existing precedent for exactly this category in `graph/builder.py`), not
red/green TDD.

**Shape**, in `src/lagmatrix/graph/ingest_builder.py`, `build_ingest_graph()`:

```
START -Send(bars batches)-> fetch_bars_node -> build_comovement_graph_node -+
      -Send(symbol batches)-> fetch_news_node -> embed_vectors_node --------+-> report_node -> END
```

Two independent branches fan out from `START` in parallel — the same D-09
pattern the live pipeline already uses for `leader_state`/`vector_retriever`
(the bars branch needs nothing from the news branch, and vice versa) — and
join trivially at `report_node`, which needs both to have finished but merges
nothing state-shaped (unlike `context_fusion`, there is no evidence-weighting
join here, so D-89's join-superstep hazard does not apply: `report_node` has
no partial-state failure mode to guard against, since it only reads two
already-complete summary dicts).

- **`fetch_bars_node`**: `Send`-fanned over `BATCH=200`-sized symbol chunks
  (reusing `fetch_bars.py`'s existing batch size), each chunk an independent
  Alpaca call. `RetryPolicy(max_attempts=3)` on the node — the same primitive
  `vector_retriever` already carries, applied here to genuine transient
  Alpaca failures rather than hand-rolling a second retry loop.
- **`fetch_news_node`**: `Send`-fanned over symbol batches, calling
  `load_news.py`'s per-symbol logic. **No `RetryPolicy` here** — `load_news.py`
  already retries transport failures internally via `with_retry` (exponential
  backoff, 6 attempts); stacking LangGraph's `RetryPolicy` on top would retry
  an already-retried failure a second, redundant way. Declined explicitly,
  not merely omitted.
- **`embed_vectors_node`**: plain, single node, sequential after
  `fetch_news_node` — needs the freshly-loaded news rows to embed, and is one
  corpus-wide chunked job internally, not a candidate for further fan-out.
- **`build_comovement_graph_node`**: plain, single node, sequential after
  `fetch_bars_node` — one dense `1,573 × 1,573` correlation matrix, computed
  in well under a second; `Send`-fanning it would add coordination overhead
  for a computation cheap enough that parallelism buys nothing (KISS: this is
  the "wrapping a linear step in `Send` for its own sake" case the brief warns
  against).
- **`report_node`**: plain, joins both branches, returns a summary dict (rows
  fetched, edges written, watermarks advanced) — the ingest job's equivalent
  of `check_baseline.py`'s pass/fail report.

**Checkpointing**: `AsyncSqliteSaver` at `data/ingest-checkpoints.sqlite` — a
separate file from the live pipeline's `data/checkpoints.sqlite`, so a large
batch ingest run's state cannot collide with or bloat the interactive
pipeline's checkpoint store. `thread_id = f"ingest-{run_date.isoformat()}"`:
a retried run for the same date reuses the same thread and resumes; a new
date gets a fresh thread automatically, with no manual cleanup step.

**Declined, matching D-35's precedent for the same reasons:** `CachePolicy`
(no node here is invoked twice with identical arguments within one run — a
batch job, not a per-candidate fan-out with repeat candidates — so there is
nothing to hit); subgraphs (five nodes does not warrant decomposition);
`Command` and `defer=True` (nothing here needs conditional control flow beyond
the two-branch fan-out `Send` already provides).

- TASK-5.1 (test) Write `tests/test_ingest_graph.py`, mirroring
  `tests/test_checkpointing.py`'s structure: inject fake `fetch_bars`/
  `fetch_news` functions (via a context object, the same `Runtime[Context]`
  pattern `LagMatrixContext` uses) over a tiny 2-batch synthetic universe.
  - `test_graph_compiles_and_runs_to_completion`: `build_ingest_graph()`
    compiles; `ainvoke` over the fake context reaches `report_node` and the
    summary dict has the expected counts.
  - `test_send_fans_out_one_branch_per_batch`: with a 2-batch fixture, assert
    the fake bars-fetch function is called exactly twice, once per batch.
  - `test_crash_and_resume_skips_completed_batches`: run with a checkpointer,
    force an exception after the first bars batch completes (a fake that
    raises on its second call), assert the run halts; re-`ainvoke` with the
    same `thread_id`, assert the fake fetch function's *first* batch is not
    called again — the concrete, falsifiable form of success criterion 5.
- TASK-5.2 (impl) `src/lagmatrix/graph/ingest_builder.py` as shaped above.

**What would make this wrong:** if a fake-injected test passes while the real
node functions (which do real I/O) do not actually resume correctly — e.g. if
`fetch_daily_bars.py`'s watermark read happens once at process start rather
than being re-read per resumed invocation. Guarded by TASK-2.3's read-then-fetch
ordering already reading the watermark fresh from disk on every invocation,
not caching it across a crash.

---

## PHASE-6 — Scheduling

**Depends on:** PHASE-5.

The repo has no scheduler today, and this plan does not add one. The minimum
that satisfies "runs daily" is **cron**, on whatever host already has network
access to Alpaca, the homelab SSH route, and the ArangoDB tunnel — no new
infrastructure invented.

- TASK-6.1 (script exemption) `scripts/run_daily_ingest.py`: thin CLI
  (`argparse`, no flags beyond `--thread-id` for manual resume, mirroring
  `run_pipeline.py`'s shape) that builds the ingest graph, invokes it with
  `thread_id = f"ingest-{date.today().isoformat()}"` unless overridden, prints
  `report_node`'s summary, and exits non-zero if any node's summary carries an
  error. Docstring's `Usage:` line states the intended cron schedule inline —
  the same place every other script in `scripts/` documents its own usage,
  rather than a new standalone docs file.
- TASK-6.2 (script exemption) Add `"run_daily_ingest"` to
  `tests/test_scripts_import.py`'s `SCRIPTS` list.
- TASK-6.3 (operational, not a code change) The crontab line, run on
  whichever host is chosen to host the job:

  ```
  0 6 * * 1-5  cd /path/to/lag-equity-matrix-ai && uv run python scripts/run_daily_ingest.py >> logs/ingest.log 2>&1
  ```

  06:00 UTC, weekdays — after Alpaca's EOD bars and Benzinga's overnight
  article settlement (US market close ~21:00 UTC the prior day), before the
  next session opens. Every step's watermark-based idempotency (PHASE-1
  through PHASE-4) means a missed day is caught up automatically the next
  time cron fires — no separate catch-up logic is built, because none is
  needed. If a scheduled workflow through `.github/workflows/` (which this
  repo already has for CI) is wanted later, that is a real, cheap upgrade path
  — not built now, since the repo runs nowhere that GitHub Actions could reach
  the homelab SSH route or the ArangoDB tunnel without new infrastructure this
  plan is not asked to build.

**What would make this wrong:** if the chosen host's cron does not carry the
same environment (`ALPACA_API_KEY`, `LAGMATRIX_ARANGO_*`, SSH agent forwarding
for the homelab route) that an interactive shell has — the classic cron
failure mode. `run_daily_ingest.py`'s `Usage:` docstring should say so
explicitly and point at `.env` (D-08: all config through `Settings`), so the
first debugging step is documented rather than rediscovered.

---

## PHASE-7 — Decision log and close-out

**Docs-only, no TDD.**

- TASK-7.1 Check `docs/spikes/overall.md`'s current maximum `D-nn` before
  writing (concurrent work from other sessions may have advanced past D-97) —
  do not hardcode a number in this plan. Via the `spike-log` skill, append one
  decision covering: the loader-idempotency fix and its measured outcome; the
  co-movement graph's two-piece design (nightly recompute, periodic
  recalibration) and why; the LangGraph ingest job's shape, citing which
  primitives it reuses from D-35 and which it declines and why; and the cron
  scheduling decision.
- TASK-7.2 If `README.md` describes the loaders as full-rebuild anywhere,
  correct it in the same commit (surgical — only the sentences that are now
  wrong, per CLAUDE.md's "touch only what you must").
- TASK-7.3 Log two open questions rather than deciding them silently: (a)
  universe refresh cadence for `data/bars-10y.parquet` (new IPOs, delistings —
  explicitly out of scope in PHASE-2), and (b) whether the live
  `graph_retriever.py` node should eventually read `moves_with` instead of
  recomputing correlation in-memory per request — explicitly out of scope
  here (see the plan header), but worth a Q-nn so it is not forgotten.

---

## Out of scope, stated rather than silently dropped

- Re-plumbing `graph_retriever.py`/`Assessment` to read from `moves_with`
  instead of its own in-memory correlation. D-34's "no ArangoDB yet" reasoning
  no longer holds (D-95 is exactly the case D-34 said would justify it), but
  changing the live pipeline's data path is a separate decision affecting
  every published corroboration-mode result, the same category of change
  Q-38 parked for the same reason. Logged as Q-nn in PHASE-7, not decided here.
- Any change to `LagEdge`, `ArangoTopology`, or the `leader-in` plan's supply
  graph. This plan adds a second, independent edge collection; it does not
  touch the first.
- A UI surface for `moves_with` edges. This plan is the ingest and graph
  construction layer only; a symbol-in page reading co-movement edges is a
  presentation decision for a future plan.
- Backfilling `moves_with`'s calibration bands to any pair below `|corr| >=
  0.3` under any circumstance, including a user request for "show me
  everything" — the constraint in "What this does not claim" is binding, not
  a default that a future flag should be able to override.

## Halt conditions

- If `TASK-1.3`'s live-gated test cannot connect to any ArangoDB instance in
  this environment, the phase is not silently marked done via the
  synthetic-only tests — re-run with `LAGMATRIX_REQUIRE_LIVE=1` and get a
  loud failure or a real pass before proceeding to PHASE-3/4/5, all of which
  depend on ArangoDB actually being reachable in whatever environment runs
  this job for real.
- If TASK-4.3's real-data run does not reproduce D-95's `0.640` figure within
  a small tolerance (say, |diff| > 0.02), halt PHASE-4 rather than adjusting
  the calibration script until it matches — a mismatch means the
  productionised script diverges from D-95's methodology somewhere, and that
  divergence needs to be found and understood, not papered over by
  re-tuning against the answer it is supposed to reproduce.
- If the `pct_identical_returns` assumption in PHASE-4 does not separate the
  known artefact from the known share classes on real data, halt and revise
  the discriminator before shipping any exclusion list — do not ship a screen
  known to misclassify its own worked examples.
- If satisfying PHASE-1's idempotency requirement turns out to need changing
  `bulk()`'s `overwriteMode` in a way that would alter previously-loaded
  `supplies_to`/`co_mentioned` documents already in the real `lagmatrix`
  database (e.g. a field a past load set that a new deterministic-key load
  would now consider "missing" and blank via `"replace"` semantics), halt and
  surface it — do not run TASK-1.2's new code against the real database
  before confirming this in a throwaway copy first.
