# PLAN-2026-09-12-relatedness-and-sectors

**Goal:** Ingest SEC SIC/sector labels onto `equity` vertices and wire both
sector-match and a sign-preserving three-state co-mention-PMI encoding into
`QuantPerspective`, so the two strongest signals found in D-135/D-136 reach
the deterministic quant read instead of sitting unused in Postgres/SEC data.

**Decisions this depends on:** D-136 (co-mention PMI has two opposite-signed
effects — having an edge predicts failure, -29.6pp CI [-40.2,-17.9]; among
edged pairs, higher PMI predicts retention, AUC 0.7451 CI [0.6105,0.8636],
replicated on three untouched bands — and a single numeric column averages
them to nothing, which is D-135's own mistake corrected). D-16 (point-in-time
correctness is a first-class constraint in this repo; addressed below for
SIC specifically, since it does not carry a `filing_date` the way
`supplies_to` does). D-97 (loaders upsert on a deterministic key and never
drop/truncate a collection — governs PHASE-3's design and the hazard flagged
under PHASE-3). Q-61 (pydantic models here inherit `extra='ignore'`; an
unrecognised constructor kwarg vanishes with no error, so every new field
must land in `domain/models.py` itself and every test must assert the actual
value, never merely `is not None`).

**Open questions that could invalidate it:** none block starting. Two new
ones are raised by this plan and are owed to `/spike-log open` at execution
time rather than resolved here (see PHASE-1 and the note under PHASE-3):
whether SEC's submissions endpoint exposes historical SIC reclassification
(bounds the size of the point-in-time approximation this plan makes), and
whether `scripts/load_arango.py`'s `bulk()` helper's `overwriteMode:"replace"`
write to `equity` would silently erase the `sic`/`sic_desc` fields this plan
adds, on a future re-run of that script. Neither blocks this plan's own
success criteria; both are real risks to note rather than bury (CLAUDE.md
§1) and are listed again under Halt conditions.

## Requirements

- REQ-1: A one-shot, re-runnable script ingests current SEC SIC/`sicDescription`
  onto `equity` vertices as `sic`/`sic_desc`, upserting — never dropping,
  truncating, or full-replacing a document that already carries other fields.
- REQ-2: `ArangoTopology` gains two read-only methods — a symbol's SIC code,
  and the PMI of every `co_mentioned` edge incident to a symbol — with the
  AQL confined to `adapters/arango.py` per its own docstring convention.
- REQ-3: `QuantPerspective` gains fields for same-2-digit-SIC match rate and
  a three-state (no-edge / weak / strong) co-mention encoding, computed by
  `compute_quant_perspective` and populated only when `arango_topology` is
  not `None` — matching the existing "`None` disables the feature" contract
  already established for every other optional `LagMatrixContext` field.
- REQ-4: The weak/strong PMI cutoff is a number derived from co-mention data
  in a correlation band that did **not** produce the measured 0.7451 AUC
  (D-136's discovery band is 0.4-0.5), so the cutoff is not fitted on the
  same sample that justified building this feature.
- REQ-5: Out of scope, touched by nothing in this plan: `assess()`'s verdict
  logic; `_brief`/`QUANT_SYSTEM_PROMPT`/`analyse_quant` (D-135 measured that
  *more* evidence made Haiku's ranking worse, 0.6216 vs 0.6484 — this plan
  does not repeat that mistake by handing the new fields to the LLM); the
  two-half vs. four-quarter stability split (D-135 measured quarters better,
  0.6958 vs 0.6654, but the owner did not ask for that change here).

## Success criteria

1. `uv run pytest -q` reports **≥ 419 passed, 0 failed** against today's
   baseline of 407 passed/1 skipped (measured on this checkout before writing
   this plan) — PHASE-3 adds ≥4 tests, PHASE-4 adds ≥4, PHASE-5 adds ≥8/-ish;
   exact counts are pinned per-phase below. Nothing existing is deleted or
   weakened.
2. `uv run ruff check` is clean after every phase.
3. `python -c "from lagmatrix.domain.models import QuantPerspective as Q; \
   print(sorted(Q.model_fields))"` lists `sector_match_pct`,
   `comention_weak_count`, `comention_strong_count` alongside the existing
   fields.
4. A live query against the `lagmatrix` ArangoDB (`FOR v IN equity FILTER
   v.sic != null COLLECT WITH COUNT INTO c RETURN c`) returns a count that is
   at least 2,100 (≈ the measured 2,163/2,183 = 99.08%, allowing for a small
   drift in the universe since D-136's measurement) — proving PHASE-3 landed
   real data, not just passed its own unit tests.
5. `docs/spikes/overall.md` carries a new `D-nn` entry (next available after
   D-136, expected D-137) citing the 2,163/2,183 coverage figure, the
   same-sector retention deltas (+23.1pp / +28.4pp / +29.6pp across bands),
   and the AUC lift (0.7251 → 0.7455) — logged via `/spike-log open`, not
   hand-edited.
6. `grep -n "PMI_STRONG_THRESHOLD" src/lagmatrix/graph/nodes/quant_perspective.py`
   shows a module-level constant with a comment naming the band, `n`, and the
   computed value it came from.
7. Running `scripts/load_sectors.py` twice in a row against the same live
   database leaves the `equity` collection's document count and every
   `sic`/`sic_desc` value unchanged the second time (idempotency, checked by
   a live-gated test in PHASE-3, not merely asserted).

## PHASE-1 — Log the sector-coverage measurement (spike-log entry)

**Exemption: pure documentation, no code or behaviour changes — CLAUDE.md's
"phases that legitimately skip TDD" (documentation) applies directly.**

**Depends on:** nothing in this plan; the measurement is already done (per
this plan's brief) and only needs recording before more work is built on top
of an unlogged fact.

**Completion criterion:** `docs/spikes/overall.md` contains a new decision
entry (expected `D-137`) with the coverage/AUC figures below, added via the
`spike-log` skill's `/spike-log open`, not a manual edit.

- TASK-1.1 (doc): Run `/spike-log open` and record: SEC SIC codes resolved
  for 2,163 of 2,183 universe symbols (99.9% match rate against
  `company_tickers.json`); same-2-digit-SIC-major-group retention lift
  +23.1pp (67.3% vs 44.2%) in band 0.4-0.5 with 98.8% pair coverage, CI
  [+20.4,+25.0], replicated at +28.4pp and +29.6pp on two other bands;
  adding sector to the full deterministic feature set lifts held-out AUC
  0.7251 → 0.7455 (third-largest weight in the fit); `equity` vertices
  today carry only `_id`/`_key`/`_rev`/`symbol` — no sector field exists yet.
  Cite D-135/D-136 as the entries this measurement corrects/extends.
- TASK-1.2 (doc): In the same entry, record the two open questions this
  plan raises but does not resolve (SIC point-in-time bound; `load_arango.py`
  `bulk()`'s replace-mode clobber risk to `sic`/`sic_desc` — see PHASE-3's
  note) as new `Q-nn` rows in the Open Questions table, expected `Q-62` and
  `Q-63`.

**Halt condition:** if `/spike-log open` finds the measurement already
logged (e.g. a parallel session logged it first), do not create a duplicate
entry — link this plan to the existing one instead.

## PHASE-2 — Determine the weak/strong PMI cutoff (REQ-4)

**Exemption: one-shot analysis script under `scripts/`, producing a constant
for later phases to consume — no repository behaviour changes here, matching
CLAUDE.md's "one-shot scripts" exemption. It is not exempt from being
*correct*: TASK-2.2's number must be computed, never guessed.**

**Depends on:** nothing else in this plan; needs a reachable ArangoDB (for
`co_mentioned.pmi`) and `data/bars-10y.parquet` (to recompute each pair's
correlation magnitude and place it in a band) — both already used elsewhere
in this repo's scripts, so no new credential or transport is introduced.

**Completion criterion:** `uv run python scripts/measure_pmi_threshold.py`
prints the band used, `n` (edged pairs in that band), and the resulting
median PMI, and exits 0.

- TASK-2.1 (analysis): Write `scripts/measure_pmi_threshold.py`. It must
  read PMI from the **0.3-0.4** correlation-magnitude band specifically —
  not D-136's discovery band (0.4-0.5, the one that produced the measured
  0.7451 AUC) and not either band built from a corr-magnitude range that
  overlaps 0.5-0.6/0.5-1.01 (those two are not disjoint from each other, so
  combining them would double-count pairs); 0.3-0.4 is the one band fully
  disjoint from every band D-136 used to measure the effect, and it has the
  largest `n` (103 edged pairs) of the three untouched bands. For each pair
  in that band with a `co_mentioned` edge, take its `pmi`. Print
  `len(pmis)`, and the median. **State the assumption inline in the
  script's own docstring:** a median split is chosen over any other
  percentile because it needs no further tuning parameter and is symmetric
  — this is a deliberate, stated choice, not a default arrived at by not
  choosing.
- TASK-2.2 (record): Take the printed median and hardcode it as
  `PMI_STRONG_THRESHOLD` in `src/lagmatrix/graph/nodes/quant_perspective.py`,
  with a comment: `# median PMI among the 103 edged pairs in the 0.3-0.4
  correlation band (scripts/measure_pmi_threshold.py) -- NOT the 0.4-0.5
  band D-136 measured the 0.7451 AUC on.` This constant is consumed by
  PHASE-5; PHASE-2 only produces and records the number.

**Halt condition:** if `co_mentioned` or `bars-10y.parquet` is unreachable,
or if the 0.3-0.4 band has fewer than ~20 edged pairs (too few to trust a
median), halt and raise it as an open question for `/spike-log open` rather
than falling back to an arbitrary round number or to the discovery band.

## PHASE-3 — `scripts/load_sectors.py`: ingest SIC onto `equity` (REQ-1)

**Exemption, split in two, following this repo's own D-97 precedent
(`tests/test_loader_idempotency.py`):** the SSH/kubectl/`arangosh` transport
and the SEC HTTP fetch are infrastructure wiring with no independently
testable behaviour from here (the same accepted gap Q-43 records for
`load_arango.py`/`load_vectors.py`) — exempt. The **document-shaping and
JS-generation functions are pure and deterministic**, exactly like
`supply_edge_docs`/`comention_edge_docs` before them, and get full red/green
tests.

**Depends on:** nothing else in this plan (independent of PHASE-2, and only
loosely ordered before PHASE-4 for schema realism — PHASE-4's tests seed
their own disposable database and do not depend on this phase's writes
existing).

**Completion criterion:** `uv run pytest tests/test_load_sectors.py -q`
passes (target: 4 new tests — 3 pure, 1 live-gated); `uv run ruff check`
clean; then, run live: `uv run python scripts/load_sectors.py` against the
real `lagmatrix` database, followed by the query in success-criterion 4.

- TASK-3.1 (test, red): Create `tests/test_load_sectors.py`. Following
  `load_edgar.py`'s convention (`UA = {"User-Agent": "lag-equity-matrix-ai
  research ridopark@gmail.com"}`, `Throttle` token bucket, `SEC_RATE = 8.0`,
  `ThreadPoolExecutor`), the script fetches `company_tickers.json` once for
  ticker→CIK, then `https://data.sec.gov/submissions/CIK{cik}.json` per
  symbol, reading the **top-level** `sic`/`sicDescription` fields (not the
  per-filing `filings.recent` array `load_edgar.py` reads). Import
  `sector_update_docs` from `load_sectors` — this import fails with
  `ImportError` today (module doesn't exist), which is this task's RED.
  `test_sector_update_docs_shapes_deterministic_key_and_preserves_symbol`:
  given rows `[("AAPL", "3674", "Semiconductors")]`, assert the returned doc
  is `{"_key": "AAPL", "symbol": "AAPL", "sic": "3674", "sic_desc":
  "Semiconductors"}` — proving `symbol` is carried in the write payload
  itself (not merged from Arango's side), which matters because the merge
  write in TASK-3.4 must not depend on `symbol` already being present on an
  existing document. **Falsifies if:** `_key` is anything other than the
  bare symbol (breaks matching the existing `equity/{symbol}` convention
  every other adapter/loader uses), or `symbol` is missing from the doc.
- TASK-3.2 (test, red): `test_sector_update_docs_skips_unresolved_symbols`
  — a row with `sic=None` (SEC had no match, the ~0.1% of the universe
  measured missing) is excluded from the returned list entirely, not
  written as a doc with `sic: null`. **Falsifies if:** a null-SIC row
  produces a doc that would overwrite a symbol's real SIC with null on a
  future re-run before that symbol resolves.
- TASK-3.3 (test, red): `test_upsert_js_never_drops_and_never_full_replaces`
  — assert the JS/AQL text the script would send does not contain `_drop`,
  and does not contain `overwriteMode:"replace"` — it must use
  `overwriteMode:"update"` (merge semantics: adds/overwrites named fields,
  leaves every other field on an existing document untouched, and still
  inserts a fresh document for a `_key` that does not exist yet — the
  ArangoDB behaviour this task pins). **Falsifies if:** the write reuses
  `load_arango.py`'s `bulk()` helper unmodified (its `overwriteMode:"replace"`
  would strip every field except `_key`/`symbol` off any `equity` document it
  touches, exactly the destructive write REQ-1 forbids) — this is *why*
  `load_sectors.py` needs its own small write helper rather than importing
  `bulk()`.
- TASK-3.4 (test, red, live-gated): `test_upsert_merges_sic_without_erasing_symbol`
  — mirroring `test_arango_topology.py`'s disposable-database pattern
  (`arango_db_or_skip`, a `test_`-prefixed scoped name, skip cleanly if
  unreachable). Seed `equity/ZZZ1` with `{"_key": "ZZZ1", "symbol":
  "ZZZ1"}`. Run the script's upsert function against it with `sic="9999"`,
  `sic_desc="Test"`. Assert the stored document afterward has **all three**
  fields — `symbol` still `"ZZZ1"`, `sic == "9999"`, `sic_desc == "Test"`.
  This is the test that actually exercises the anti-clobber property end to
  end, not just the generated-text check in TASK-3.3. **Falsifies if:** the
  document loses `symbol` (proves a `"replace"`-mode write reached
  production) or gains no `sic` (proves the merge never happened).
- TASK-3.5 (impl, green): Write `scripts/load_sectors.py`: `sector_update_docs`,
  a small `upsert_sectors(docs)` helper (its own `arango_js` call with
  `overwriteMode:"update"`, not a modification to `load_arango.py`'s `bulk()`
  — see the note below on why that helper is deliberately *not* touched),
  the SEC fetch (mirroring `load_edgar.py`'s `http`/`Throttle`/thread-pool),
  and a `main()` reading the universe from `data/bars-10y.parquet`'s
  `symbol` column (the 2,183-symbol correlation universe D-136's coverage
  figure was measured against — not `data/universe.csv`'s broader 3,203, and
  not the 514-vertex `equity` collection, which undercounts the universe by
  design per D-136's own root-cause finding). Prints resolved/unresolved
  counts and the coverage percentage on completion.
- TASK-3.6 (verify): `uv run pytest tests/test_load_sectors.py -q` and
  `uv run ruff check`, both outputs pasted verbatim per D-37's convention.
  Then run live and confirm success-criterion 4's query.

**Note — a hazard this phase deliberately does not fix:**
`scripts/load_arango.py`'s `bulk()` writes `equity` documents with
`overwriteMode:"replace"` (a full document replace, not a merge). If that
script is ever re-run after this one (e.g. folded into
`scripts/daily_ingest.py`'s nightly chain), its `bulk("equity", [{"_key": s,
"symbol": s} for s in sorted(syms)])` call will silently strip `sic`/
`sic_desc` back off every `equity` document it touches, because a
`"replace"`-mode write is exactly the payload it sends — `{_key, symbol}`
only. Fixing `load_arango.py` is out of this plan's stated scope ("exactly
these two things, nothing more"); the hazard is flagged here, in PHASE-1's
spike-log entry, and again under Halt conditions, rather than silently
patched or silently ignored.

## PHASE-4 — `ArangoTopology.sic_of` / `.comention_pmi` (REQ-2)

**Exemption: none — new read behaviour, TDD mandatory.**

**Depends on:** nothing else in this plan (independent of PHASE-3's data;
these tests seed their own disposable database, matching
`test_arango_topology.py`'s existing pattern exactly).

**Completion criterion:** `uv run pytest tests/test_arango_relatedness.py -q`
passes (target: 5 new tests, all live-gated, skip cleanly if ArangoDB is
unreachable — D-38's "skip, don't pass vacuously" convention); `uv run ruff
check` clean.

- TASK-4.1 (test, red): Create `tests/test_arango_relatedness.py`, mirroring
  `test_arango_topology.py`'s fixture style (own disposable `test_`-prefixed
  database, fictional symbols per that file's own stated reason — real
  `supplies_to`/`co_mentioned` edges have been found not to be clean ground
  truth). Seed `equity/SYMA` with `sic="3674"`, `equity/SYMB` with
  `sic="3674"`, `equity/SYMC` with no `sic` field at all.
  `test_sic_of_returns_resolved_symbols_only`: `topology.sic_of(["SYMA",
  "SYMB", "SYMC", "SYMD"])` (`SYMD` doesn't exist as a vertex at all) equals
  `{"SYMA": "3674", "SYMB": "3674"}` — `SYMC` and `SYMD` both simply absent
  from the dict, not mapped to `None`. **Falsifies if:** `SYMC`/`SYMD`
  appear as keys with a `None` value (forces every caller to handle two
  different "unknown" representations), or `SYMA`'s value comes back
  wrong.
- TASK-4.2 (test, red): Seed `co_mentioned` edges `SYMA~SYMB` (`pmi=1.2`)
  and `SYMB~SYMC` (`pmi=0.4`). `test_comention_pmi_finds_edges_in_either_direction`:
  `topology.comention_pmi("SYMB")` equals `{"SYMA": 1.2, "SYMC": 0.4}` —
  both directions found from one query, and the *other* symbol (not the
  query symbol) is the key. **Falsifies if:** only one direction is found
  (an AQL filter checking `_from` alone), or the key/value are swapped.
- TASK-4.3 (test, red): `test_comention_pmi_empty_for_a_symbol_with_no_edges`
  — `topology.comention_pmi("SYMA_LONER")` (vertex exists, no edges) is
  `{}`, not an error. **Falsifies if:** this raises, or returns a dict
  containing the symbol itself.
- TASK-4.4 (test, red): `test_comention_pmi_empty_when_collection_absent`
  — against a freshly created disposable database with no `co_mentioned`
  collection at all, `topology.comention_pmi("ANY")` is `{}` (mirrors
  `movers_with`'s existing `if not db.has_collection(...): return []`
  guard). **Falsifies if:** this raises `CollectionNotFoundError` instead
  of degrading, which would take down `compute_quant_perspective` for any
  fresh database that hasn't run `load_arango.py` yet.
- TASK-4.5 (impl, green): Add `sic_of(self, symbols: list[str]) ->
  dict[str, str]` and `comention_pmi(self, symbol: str) -> dict[str, float]`
  to `ArangoTopology` in `src/lagmatrix/adapters/arango.py`, with the AQL as
  module-level constants (`_SIC_OF_AQL`, `_COMENTION_PMI_AQL`) next to the
  existing `_LAGGERS_OF_AQL`/`_MOVERS_WITH_AQL`, keeping every query in this
  module per its own docstring.
- TASK-4.6 (verify): `uv run pytest tests/test_arango_relatedness.py -q` and
  `uv run ruff check`, pasted verbatim.

## PHASE-5 — Wire relatedness into `compute_quant_perspective` (REQ-3, REQ-4)

**Exemption: none — new behaviour in a function every candidate's
assessment already flows through. Also the phase Q-61 applies to directly:
every new field must land in `QuantPerspective` itself, and every test below
asserts an actual computed value.**

**Depends on:** PHASE-2 (`PMI_STRONG_THRESHOLD` must exist), PHASE-4
(`sic_of`/`comention_pmi` must exist — this phase's tests use a lightweight
fake object implementing both methods, not a live database, matching how
`llm`/`bars` are faked elsewhere in this test suite; no live-Arango
dependency here).

**Completion criterion:** `uv run pytest tests/test_quant_perspective.py
tests/test_quant_wiring.py -q` passes (target: existing tests plus 8 new —
6 in `test_quant_perspective.py`, 2 in `test_quant_wiring.py`); `uv run ruff
check` clean.

- TASK-5.1 (test, red): In `tests/test_quant_perspective.py`, add a
  `_FakeTopology` test double with `.sic_of(symbols) -> dict` and
  `.comention_pmi(symbol) -> dict` returning whatever the test constructs
  (no I/O). `test_arango_topology_none_disables_all_three_relatedness_fields`:
  call `compute_quant_perspective(...)` with `arango_topology=None` and
  correlation edges present; assert `sector_match_pct is None`,
  `comention_weak_count is None`, `comention_strong_count is None` — all
  three, together, the same "`None` disables the feature" contract every
  other optional context-derived value in this repo honours. **Falsifies
  if:** any of the three defaults to `0`/`0.0` instead of `None` when the
  feature is off, which would be indistinguishable from "measured, found
  nothing."
- TASK-5.2 (test, red): `test_sector_match_pct_counts_same_2digit_sic_prefix`
  — candidate `CAND` (fake topology: `sic_of` returns `{"CAND": "7372",
  "LEAD1": "7371", "LEAD2": "3674"}`), two correlation edges to `LEAD1`
  (same major group "73") and `LEAD2` (different, "36"). Assert
  `sector_match_pct == 50.0`. **Falsifies if:** the comparison uses the full
  4-digit SIC instead of the 2-digit major-group prefix (would give `0.0`
  here, since `"7372" != "7371"` and `"7372" != "3674"` on the full code).
- TASK-5.3 (test, red): `test_sector_match_pct_none_when_candidates_own_sic_unresolved`
  — fake topology's `sic_of` omits `CAND` entirely (unresolved, the ~0.1%
  case) but resolves the leader. Assert `sector_match_pct is None` while
  `comention_weak_count`/`comention_strong_count` are still computed as
  ordinary ints (independent data source, must not be dragged down by
  missing sector data). **Falsifies if:** the whole relatedness read
  degrades to `None` together — proves sector and co-mention are wrongly
  coupled through one shared "missing" branch instead of two independent
  ones.
- TASK-5.4 (test, red): `test_comention_three_state_encoding_never_collapses_to_one_number`
  — the test this whole phase exists to make pass. Fake topology's
  `comention_pmi("CAND")` returns `{"LEAD1": PMI_STRONG_THRESHOLD + 0.5,
  "LEAD2": PMI_STRONG_THRESHOLD - 0.5}` (imported from
  `quant_perspective`, never a hardcoded literal, so the test stays valid
  regardless of PHASE-2's exact number); `LEAD3` has a correlation edge but
  no entry in `comention_pmi`'s returned dict at all (no co-mention edge
  exists for that pair). Assert `comention_strong_count == 1`,
  `comention_weak_count == 1`, and — the collapse-guard — that `LEAD3`'s
  absence increments **neither** counter (so `n_edges=3` but
  `weak+strong=2`, proving "no edge" is a distinct third state, not folded
  into "weak" as PMI-zero would). **Falsifies if:** a future edit replaces
  the two counts with one signed/averaged number (the exact D-135 mistake
  this feature exists to not repeat), or treats a missing PMI entry as
  weak.
- TASK-5.5 (test, red): `test_quant_perspective_model_round_trips_new_fields`
  (Q-61 guard) — construct `QuantPerspective(n_edges=1, median_ci_width=0.1,
  duplicate_count=0, split_half_sign_agree_pct=100.0, split_half_min_abs=0.2,
  candidate_is_etf=False, note="x", sector_match_pct=50.0,
  comention_weak_count=1, comention_strong_count=2)` directly and assert
  `result.sector_match_pct == 50.0`, `result.comention_weak_count == 1`,
  `result.comention_strong_count == 2`. **Falsifies if:** any field name in
  the model doesn't match the constructor kwarg exactly — `extra='ignore'`
  (Q-61) would let this construct successfully and silently drop the value,
  so the assertion on the *value*, not `hasattr`, is what catches it.
- TASK-5.6 (impl, green): Add `sector_match_pct: float | None`,
  `comention_weak_count: int | None`, `comention_strong_count: int | None`
  to `QuantPerspective` in `src/lagmatrix/domain/models.py`. In
  `compute_quant_perspective` (`src/lagmatrix/graph/nodes/quant_perspective.py`),
  add a trailing `arango_topology: object | None = None` parameter (default
  preserves every existing direct call site) and, when it is not `None`:
  initialise `comention_weak_count = comention_strong_count = 0`; if
  `correlation_edges` is non-empty, call `sic_of([candidate.symbol] +
  [e.leader for e in correlation_edges])` once and `comention_pmi(candidate.symbol)`
  once (not per-edge — bounds the read to two calls per candidate
  regardless of edge count); compute `sector_match_pct` only if the
  candidate's own SIC resolved, comparing `sic[:2]` prefixes; for each
  correlation edge, look up its leader's PMI and bucket into
  `comention_strong_count` (`pmi >= PMI_STRONG_THRESHOLD`) or
  `comention_weak_count` (`pmi < PMI_STRONG_THRESHOLD`) — an edge with no
  PMI entry at all increments neither.
- TASK-5.7 (impl, green): In `quant_perspective()` (same module), pass
  `arango_topology=runtime.context.arango_topology` through to
  `compute_quant_perspective`. `LagMatrixContext` needs no change — the
  `arango_topology: object | None = None` field already exists.
- TASK-5.8 (test, red then green): Extend `tests/conftest.py`'s `run_graph`
  fixture to accept `arango_topology=None` (mirroring the existing `bars=None`
  parameter, one line) and thread it into `LagMatrixContext(...)`. Add two
  tests to `tests/test_quant_wiring.py`: `test_no_arango_topology_leaves_relatedness_fields_none`
  (`run_graph([...])` with the default `arango_topology=None`; assert
  `assessments[0].quant.sector_match_pct is None`, end to end through the
  compiled graph, not just the unit-level call) and
  `test_injected_topology_surfaces_actual_relatedness_values` (inject the
  same `_FakeTopology` used in PHASE-5's unit tests via `run_graph(...,
  arango_topology=fake)`; assert the *specific* expected
  `sector_match_pct`/count values on `assessments[0].quant`, not merely that
  they are not `None` — this is the Q-61-shaped wiring guard, since a
  forgotten `arango_topology=` at the `quant_perspective()` call site would
  otherwise leave the fields silently `None` while every other test still
  passes). **Falsifies if:** the injected topology's values never reach
  `Assessment.quant` (proves TASK-5.7 was skipped or the context wiring is
  wrong), or the "none disables" test regresses.
- TASK-5.9 (verify): `uv run pytest tests/test_quant_perspective.py
  tests/test_quant_wiring.py -q` and `uv run ruff check`, pasted verbatim.
  Then `uv run pytest -q` for the whole-suite count in success-criterion 1.

## Point-in-time correctness (D-16) — addressed, not glossed over

SIC has no `filing_date` the way `supplies_to` does; SEC's submissions
endpoint (`https://data.sec.gov/submissions/CIK{cik}.json`) returns only the
company's **current** SIC, with no history of past reclassifications. Two
consequences, stated rather than assumed away:

- **This plan introduces no bias beyond what D-136's own measurement
  already assumed.** D-136's +23.1pp/AUC-0.7455 figures were themselves
  computed by joining today's SIC onto historical band pairs — the same
  join this plan wires into production. Production cannot be more biased
  than the measurement that justified building it, because it is the same
  join run at a later date.
- **What is unverified, and left unverified on purpose:** whether any
  meaningful fraction of the universe *reclassified* SIC during the
  backtest window, which would mean some historical pairs were scored
  against a sector label the company did not hold at the time. SIC
  reclassification is rare in practice (asserted here, not measured — SIC
  codes are largely static once assigned) but this plan does not verify
  that assertion, because SEC's submissions endpoint does not expose SIC
  history to check it against. This is logged as an open question (PHASE-1,
  expected Q-62) rather than either blocking this plan on it or silently
  assuming it away.

## Halt conditions

- PHASE-2: fewer than ~20 edged pairs in the 0.3-0.4 band, or the
  live data needed (Arango `co_mentioned`, `data/bars-10y.parquet`) is
  unreachable — halt and raise for `/spike-log open`, do not substitute an
  arbitrary threshold.
- PHASE-3: any generated write payload contains `_drop`, `_truncate`, or
  `overwriteMode:"replace"` against `equity` — halt immediately; this is
  the exact class of bug D-97 exists to prevent and the user's explicit
  safety constraint for this plan.
- PHASE-3/ongoing: if any future change proposes folding
  `scripts/load_sectors.py` into `scripts/daily_ingest.py`'s nightly chain
  alongside `scripts/load_arango.py` without first resolving the
  `bulk()`-replace-mode hazard flagged above, halt and resolve that
  conflict first (fix `load_arango.py`'s equity write or reorder/guard the
  chain) rather than schedule both and let sector data evaporate silently.
- PHASE-5: if `PMI_STRONG_THRESHOLD` is not yet defined when this phase
  starts (PHASE-2 skipped or halted), halt — do not hardcode a placeholder
  number "to keep moving."
- Any phase: if a test can only be made to pass by weakening an assertion
  from an actual value to `is not None` (Q-61's exact failure mode), halt
  and fix the seam instead — per CLAUDE.md, an unverifiable behaviour is a
  defect in the design, not an accepted limitation.
