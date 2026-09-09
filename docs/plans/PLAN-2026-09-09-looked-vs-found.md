# PLAN-2026-09-09-looked-vs-found

**Goal:** Add a single, inert `neighbours` count to `Assessment` so a scan
card's reader can tell "20 neighbours checked, none moved unusually" from a
bare `0 supporting · 0 contradicting · effective evidence 0` that today reads
as if nothing happened.

**Correction to the original brief, verified against real data before this
revision:** the brief described two cases as byte-identical today —
`leaders == []` ("nothing to look at") and `leaders != [] but movers == []`
("looked, found nothing") — and cited "11 of 19 candidates" as the first
case. That number is real but was **misattributed**: it is the count of
candidates with `movers == []`, not `leaders == []`. On the real 2026-05-11
scan every one of the 19 candidates has `len(leaders) == 20` — `MarketScan`'s
default `topk` (20, against a ~3,204-symbol universe) means a correlation
neighbourhood is essentially never empty. `leaders == []` is a real code path
(D-85/D-87's admit gate exists for exactly it) but does **not** occur on a
real scan today; it would take a supply-edge-only admit (D-79/D-81) with zero
correlation edges, which the fixtures below still exercise directly as a
defensive case, not as the common one. The design and the field are
unaffected by this correction — see below — but the framing and the shipped
copy are, and are corrected throughout this revision.

**Decisions this depends on:** D-87 (deleted `room`/`origin_status` as
unfounded and non-predictive; `description` is its only surviving
replacement field, and this plan adds a second field of the same *kind* —
descriptive metadata about the pipeline's own process, never a vote — sitting
beside it, not another `room`). D-85, simplified by D-87 (the admit gate
`if not leaders and not c.origin_leader: continue` is why a candidate with
zero correlation neighbours reaches `assess()` at all — this plan reads and
exposes the same `leaders` list that gate already tests, adding nothing new
to what is retrieved). D-27 (neighbourhoods are drawn from the wide universe,
never the signal's own tickers — `leaders` already respects this; the new
count inherits it for free, since it counts the same list). D-81 (only
`e.lagger == c.symbol` edges count — the same filter `leaders` already
applies; a supply edge, where the candidate is the leader, contributes 0 to
this count, which is correct: a supplier is not the candidate's neighbour in
the sense this count means). D-19 (the layer must still be able to say
"contradicted" — untouched). Q-12 (weighting, not counting, is how evidence
is measured — the reason this field must never be summed, discounted, or
otherwise fed into `effective_evidence`; it is a count of what was *looked
at*, not evidence). D-34 (no calibrated probability, ever — the same
discipline applies here: this field must not be styled or worded as a
confidence, quality, or coverage score, only as a fact about process).

**Open questions that could invalidate it:** none block starting. Q-42 (does
the supply graph buy anything over a correlation screen) and Q-44
(concurrency reshaping) are unrelated and explicitly out of scope.

## The one design decision this plan makes, and why

**What to expose: `len(leaders)` alone — not `len(movers)`, not both.**

`context_fusion.fuse_evidence` already computes, per candidate:
```python
leaders = [e.leader for e in edges_for(state, c) if e.lagger == c.symbol]
shocks = {s.symbol: s for s in leader_shocks_by_key.get(key, [])}
movers = [s for s in leaders if s in shocks]
```
Three states exist: (a) `leaders == []` — nothing to look at (defensive
only, per the correction above — not observed on a real scan); (b)
`leaders != []` but `movers == []` — looked, found nothing (the common real
case, per the corrected measurement — e.g. `ABBV`/`GILD` on the 2026-05-11
scan: 20 leaders, 0 movers); (c) `movers != []` — looked, found something.
State (c) is already distinguishable today without any new field:
`assess()` already reports `n_supporting`/`n_contradicting`, and inside
`fuse_evidence`'s `if movers:` block, **every mover unconditionally gets
exactly one `Evidence` appended** (`c_evidence.append(Evidence(kind=
"leader_move", ...))` runs once per `m in movers`, with no filter that could
skip one) — so `len(movers) == len(supporting) + len(contradicting)` always,
for every candidate that reaches `assess()`. Exposing `len(movers)` as a new
field would be exposing a number the reader can already compute from two
numbers already on the card.

The only genuinely new information is `len(leaders)`. In practice, because
case (a) does not occur, its main job is turning the common case (b) into a
legible negative result — "20 neighbours checked, none moved unusually"
instead of a bare `0 · 0 · 0` that reads as though the pipeline did nothing.
It also keeps the defensive case (a) honest for the code path that can still
produce it (a supply-edge-only admit) rather than silently rendering it
identically to case (b). That is the smallest addition that resolves the
ambiguity — "the smallest thing that answers the user's question," per the
brief — and it holds regardless of which of (a)/(b) turns out to be the
common one, which is why the correction above changes the framing and the
copy but not the field itself.

**New field, not a `rationale` rewording.** `rationale` is free-form prose,
built once in `assess()` and displayed in one place — the blockquote shown
only for a `contradicted` verdict (`serve_index.html`'s `$("#rationale")`,
gated on `a.verdict === "contradicted" && a.rationale`). The per-card summary
line (`n_supporting`/`n_contradicting`/`effective_evidence`) is built from
discrete numeric JSON fields, not parsed out of prose, and needs to render on
every card, every verdict. Putting the count in `rationale` would mean either
parsing a sentence in JS to drive the card (fragile, and the exact kind of
implicit contract this repo avoids) or duplicating the sentence's number in a
field anyway. A new `Assessment.neighbours: int` field, read the same way
`n_supporting = len(a.supporting)` already is, keeps parity with the fields
already doing this job and adds no parsing.

**Test that pins the "carries no weight" invariant** (PHASE-1, TASK-1.1 test
3): `test_neighbours_count_never_affects_effective_evidence` — the same
mover, with two extra non-moving leaders added or removed, must produce
`effective_evidence_by_key[key] == 1.0` in both cases. Falsifies if adding a
neighbour that never shocked changes the weighted evidence at all.

## Success criteria
1. `Assessment` gains `neighbours: int` — `len(leaders)` from `fuse_evidence`,
   populated for every candidate that reaches `assess()` (i.e. every key
   already present in `effective_evidence_by_key`).
2. `neighbours` never enters `effective_evidence`/`effective_evidence_by_key`
   and produces no `Evidence` of any `kind` — pinned by
   TASK-1.1 test 3 and TASK-2.1's re-assertion on
   `test_description_flows_through_to_assessment_without_affecting_verdict`.
3. `context_fusion.fuse_evidence` returns `neighbours_by_key: dict[str, int]`,
   populated in the same loop iteration and keyed identically to
   `effective_evidence_by_key`.
4. `scripts/serve.py`'s `"done"` SSE payload includes `"n_neighbours":
   a.neighbours` alongside the existing `n_supporting`/`n_contradicting`.
5. `scripts/serve_index.html`'s verdict card states, in one clause read
   before the raw counts: "`N` neighbours checked, none moved unusually" when
   `n_neighbours > 0` and `n_supporting + n_contradicting == 0` (the common
   real case, per the corrected measurement above); "`N` neighbours checked"
   when `n_supporting + n_contradicting > 0`; and, only as a defensive
   fallback not expected to fire on a real scan today, "no neighbours
   retrieved" when `n_neighbours == 0`. The existing
   `n_supporting`/`n_contradicting`/`effective evidence` text is unchanged
   and still present on every card.
6. `uv run python scripts/check_baseline.py --synthetic` prints `baseline
   unchanged: 6 rows identical` at every phase boundary (`scripts/
   capture_baseline.py`'s `COLUMNS` does not include `neighbours`, so this is
   expected to hold trivially throughout — confirmed by reading
   `capture_baseline.py:32-35` before writing this plan).
7. `uv run python -m pytest -q` passes, `128 passed, 0 skipped` (today: 124;
   PHASE-1 adds 3 to `tests/test_nodes.py`; PHASE-2 adds 1 new test to
   `tests/test_candidate_description.py` and strengthens 1 existing one
   in place, net +1).

## PHASE-1 — `context_fusion.fuse_evidence` computes and returns `neighbours_by_key`
**Depends on:** the design decision above.
**Completion criterion:** `uv run python -m pytest -q tests/test_nodes.py`
passes, `26` tests (today: 23, plus 3 new), `0` failures; `uv run python
scripts/check_baseline.py --synthetic` prints `baseline unchanged: 6 rows
identical`.

- TASK-1.0 (doc, not code): before any edit, run `/spike-log open` and log
  this plan's design decision above as its own entry (next free `D-n` —
  confirm the number with `grep -oE "^### D-[0-9]+" docs/spikes/overall.md |
  sort -t- -k2 -n | tail -1` immediately before writing, since concurrent
  work in this session may have already taken D-90). Use the assigned number
  in the `neighbours` field's docstring (TASK-1.2) and in this plan's own
  references to "D-9x" below. Content to log: what is exposed
  (`len(leaders)`, not `len(movers)`), why (the two are redundant per the
  design decision above), and the invariant it must never violate (no
  weight, never in `effective_evidence`).

- TASK-1.1 (test, `tdd-red`, `tests/test_nodes.py`, appended after the last
  existing test in the file, `test_description_never_emits_evidence`):
  1. `test_neighbours_by_key_counts_correlation_leaders_regardless_of_
     whether_they_moved(closes)`: `_candidate(sym="CAND", d=date(2026, 6,
     1))` (existing helper), three correlation `LagEdge`s
     (`leader="LEAD1"/"LEAD2"/"LEAD3"`, `lagger="CAND"`, same shape as the
     existing `corr_edge` in `test_fuse_evidence_does_not_crash_when_
     correlation_and_supply_edges_coexist`), `leader_shocks` giving only
     `LEAD1` a `Shock` (so `movers == ["LEAD1"]`, one `leader_move` unit).
     Call `fuse_evidence` directly (hand-built state, no `retrieve_
     neighbourhood`, matching this file's existing house style for
     `lag_edges_by_key`-driven tests). Assert
     `out["neighbours_by_key"][key] == 3`. Falsifies if the field is absent,
     or equals `1` (movers, not leaders).
  2. `test_neighbours_by_key_is_zero_when_only_a_supply_edge_admits_the_
     candidate(closes)`: reuse the exact fixture from the existing
     `test_fuse_evidence_admits_candidate_with_origin_leader_and_no_
     correlation_leaders` verbatim (candidate `CAND`,
     `origin_leader="LEADUP"`, `lag_edges_by_key={key:
     [_supply_edge("SUPPLIER1")]}`, `leader_shocks` giving `CAND` itself a
     `Shock`). Assert `out["neighbours_by_key"][key] == 0`. This pins the
     defensive "nothing to look at" code path (D-85/D-87's admit gate) that
     a supply-edge-only candidate still exercises — **not** the common real
     case, which is `movers == []` with `leaders` non-empty (see the
     correction in this plan's Goal section); kept because the gate still
     exists and must still behave correctly even though it is rare in
     practice. Falsifies if the count is anything other than `0`, or the key
     is absent.
  3. `test_neighbours_count_never_affects_effective_evidence(closes)`: same
     `LEAD1`/`LEAD2`/`LEAD3` fixture as test 1, run once with all three
     leaders and once with only `LEAD1` (same single `Shock`). Assert both
     runs give `neighbours_by_key[key]` of `3` and `1` respectively, but
     both give `effective_evidence_by_key[key] == 1.0` — `sub.shape[1] == 1`
     in both cases (a lone mover has no cluster to discount against, so
     `rho is None`, `bloc = 1`, `w = 1.0`, unaffected by how many other
     leaders exist and never moved). Falsifies if the two runs' effective
     evidence disagree, or either is not exactly `1.0` — either would mean a
     neighbour that never moved is changing the weighted evidence.
  - Run `uv run python -m pytest -q tests/test_nodes.py` and observe all
    three new tests fail with `KeyError: 'neighbours_by_key'` (the key does
    not exist in `fuse_evidence`'s return dict yet).

- TASK-1.2 (impl, `tdd-green`, `src/lagmatrix/graph/nodes/context_fusion.py`,
  `src/lagmatrix/graph/state.py`):
  - `context_fusion.py`: add `neighbours_by_key: dict[str, int] = {}` beside
    the other per-key dict initialisations at the top of `fuse_evidence`.
    Inside the loop, alongside the existing `evidence_by_key[key] = ...` /
    `effective_by_key[key] = ...` writes at the bottom (after `c_evidence`
    and `c_effective` are finalised, same place, same indentation level —
    do not compute it earlier where `leaders` is first assigned, so a
    reader sees all four per-key writes together), add
    `neighbours_by_key[key] = len(leaders)`. Add `"neighbours_by_key":
    neighbours_by_key,` to the returned dict, next to
    `"effective_evidence_by_key"`. Update the module docstring's opening
    line only if it now reads as inaccurate (it currently says "Weighting,
    not counting" — this field *is* a count, but of what the pipeline
    looked at, not of evidence; add one sentence distinguishing the two if
    the existing docstring would otherwise mislead a reader, nothing more).
  - `state.py`: add `neighbours_by_key: dict[str, int]` to `LagMatrixState`,
    directly below `description_by_key`, plain (not reducer-annotated,
    matching `effective_evidence_by_key`'s and `description_by_key`'s own
    declarations — no two branches ever write the same key, since each
    candidate has exactly one).

**Halt condition:** if `test_fuse_evidence_effective_evidence_never_exceeds_
raw_count` or any other pre-existing `fuse_evidence` test's result changes —
this phase must be purely additive (one new key in the return dict, one new
line inside the loop); any change to an existing assertion's outcome means
something other than the intended addition happened. Stop and find it rather
than adjusting the test.

**What would make this wrong:** if `leaders` ever gains duplicate entries for
the same symbol in the future (it cannot today — `retrieve_neighbourhood`'s
correlation edges come from `corr.abs().nlargest(topk).index`, a pandas
`Index` of distinct column labels, and supply edges never satisfy `e.lagger
== c.symbol` per D-79's orientation, confirmed by reading
`graph_retriever.py` before writing this plan) — `len(leaders)` would then
overcount. If that changes, switch to `len(set(leaders))` and say so in a
comment citing this plan, rather than silently changing the number's meaning.

## PHASE-2 — `Assessment.neighbours`; wired through `assessor.py`
**Depends on:** PHASE-1.
**Completion criterion:** `uv run python -m pytest -q tests/test_nodes.py
tests/test_candidate_description.py` passes, `30` tests (PHASE-1's `26` + `4`,
`tests/test_candidate_description.py` growing from `3` to `4`), `0`
failures; `uv run python scripts/check_baseline.py --synthetic` prints
`baseline unchanged: 6 rows identical`.

- TASK-2.1 (test, `tdd-red`, `tests/test_candidate_description.py`):
  - Strengthen the existing
    `test_description_flows_through_to_assessment_without_affecting_verdict`
    by adding `assert a.neighbours == 1` (Y is X's one correlation neighbour
    in `_origin_move_closes` — already established by that test's own
    docstring). This is the pin required by the design decision above:
    `effective_evidence == 1.0` and `verdict == "corroborated"` are already
    asserted there; adding `neighbours == 1` in the same test proves the new
    field sits beside those numbers without moving them.
  - Add a new fixture `_single_symbol_closes()`: one column (`X`) only, same
    quiet values and 12-session shape as `_origin_move_closes`'s `X` column
    with `Y` dropped entirely — `retrieve_neighbourhood`'s correlation pool
    then has nothing left once `X`'s own column is dropped (Q-26), so
    `leaders == []` genuinely, not merely filtered by weak correlation. This
    is a synthetic, defensive exercise of the `leaders == []` admit path
    (D-85/D-87) end to end — not a claim that this is the common real case;
    the corrected measurement in this plan's Goal section shows a real scan
    admits via `origin_leader` with `leaders` already at `topk` (20), never
    empty.
    ```python
    def _single_symbol_closes() -> pd.DataFrame:
        quiet_x = [0.0009, -0.0011, 0.0013, -0.0006, 0.0011, -0.0016, 0.0004]
        x = quiet_x + [0.0, 0.0, 0.0]
        idx = pd.bdate_range("2026-01-01", periods=12, tz="UTC")
        vals = [0.0, *x, 0.0]
        return pd.DataFrame({"X": 100 * np.cumprod([1 + v for v in vals])}, index=idx)
    ```
  - Add `test_neighbours_is_zero_end_to_end_when_only_the_origin_leader_
    admits_the_candidate()`: `Candidate(symbol="X", direction="up",
    as_of=date(2026, 1, 15), origin="scan", origin_leader="Y")` over
    `_single_symbol_closes()`, run through `_run_full_chain(closes, cand,
    trail=10, topk=20, move_win=3, sigma=2.0)` (existing helper — the real
    `retrieve_neighbourhood` -> `leader_state` -> `fuse_evidence` -> `assess`
    chain). Assert `a.neighbours == 0`, `a.verdict == "neutral"`,
    `a.effective_evidence == 0.0`, and `a.description is not None`
    (`leader_state`'s unconditional self-shock for `c.symbol` still gives
    `X` a `Shock` of its own, so the D-87 description mechanism populates
    even though `X` has no correlation neighbour at all — the same
    reasoning `test_description_names_the_origin_leader_and_signed_move_
    toward_the_thesis` already relies on). Falsifies if `a.neighbours` is
    anything other than `0`, or if `a.verdict`/`a.effective_evidence` show
    any sign of manufactured evidence.
  - Run `uv run python -m pytest -q tests/test_candidate_description.py` and
    observe both the strengthened test and the new test fail
    (`AttributeError: 'Assessment' object has no attribute 'neighbours'`).

- TASK-2.2 (impl, `tdd-green`, `src/lagmatrix/domain/models.py`,
  `src/lagmatrix/graph/nodes/assessor.py`):
  - `domain/models.py`: add to `Assessment`, after `description`:
    ```python
    # how many price-correlated neighbours fuse_evidence found for this
    # candidate (D-9x) -- a fact about what the pipeline looked at, not a
    # claim about the market. Carries no weight and is never summed into
    # `effective_evidence`; it exists only so a reader can tell "no
    # neighbourhood to examine" (0) from "examined, and none of them moved"
    # (>0, with `supporting` and `contradicting` both empty) -- the two
    # were byte-identical before this field existed.
    neighbours: int
    ```
    (Substitute the real `D-9x` number assigned in PHASE-1's TASK-1.0.)
  - `assessor.py`: add `neighbours_by_key = state.get("neighbours_by_key",
    {})` beside the existing `description_by_key = state.get(
    "description_by_key", {})` read, and add `neighbours=neighbours_by_key
    .get(key, 0),` to the `Assessment(...)` constructor call (same `.get(
    key, default)` defensiveness the surrounding `evidence_by_key.get(key,
    [])` already uses, for consistency — not a claim that a missing key is
    an expected case; PHASE-1 always populates it for every key that
    reaches `effective_evidence_by_key`, which `assess()`'s own loop guard
    already requires before this point).

**Halt condition:** if adding `neighbours` to the `Assessment(...)`
constructor call breaks any test that constructs an `Assessment` directly
without it (`neighbours` has no default, matching `effective_evidence`'s own
un-defaulted style) — `grep -rn "Assessment(" src/ tests/` shows only
`assessor.py`'s one constructor call site today; if that has changed by
execution time, update every site found, not just `assessor.py`.

**What would make this wrong:** if a future reader treats `a.neighbours` as
a confidence or coverage score (e.g. "more neighbours checked = more
reliable verdict") — nothing in this phase's field, docstring, or tests
implies that, and the UI copy in PHASE-3 must not imply it either. If a later
change divides anything by `neighbours` (a "hit rate" of
`supporting / neighbours`, say) — that is exactly D-87's mistake in a new
shape (an unfounded denominator); it needs its own pre-registered measurement
against forward returns before it exists, not a quiet addition here.

## PHASE-3 — Wire `neighbours` through the live server and rewrite the card copy
**Depends on:** PHASE-1, PHASE-2. Direct edit, not TDD — thin script/HTML/JS
wiring and copy reading an already-tested field, exempt per this repo's
TDD carve-out for "infrastructure wiring with no behavioural change" to the
field's own computation (the computation is PHASE-1/2's concern; this phase
only displays it) and per the team lead's brief, which names
`scripts/serve.py`/`scripts/serve_index.html` changes as exempt outright.
**Completion criterion:** `grep -c "n_neighbours" scripts/serve.py
scripts/serve_index.html` returns `>= 1` for each file; `uv run python -m
pytest -q tests/test_scripts_import.py` passes (`4` tests, unchanged —
confirms `serve.py`'s import and its entry-point surface are both still
intact after the edit, per Q-43's own warning that nothing else in the suite
would catch a broken import here); full suite still `128 passed, 0 skipped`
(no new tests this phase).

Caveat on locations: several other plans/agents may be touching
`scripts/serve.py` and `scripts/serve_index.html` concurrently in this
session. The line numbers quoted below are current as of this plan's writing
(2026-09-09) — if they no longer match at execution time, relocate by
searching for the quoted anchor text (`"n_contradicting"`, `class="lane"`,
`const esc =`) rather than trusting the line numbers.

- TASK-3.1 (`scripts/serve.py`, in `stream()`'s `"done"` emit, the per-
  assessment dict around line 232-244): add `"n_neighbours": a.neighbours,`
  next to the existing `"n_supporting": len(a.supporting),`.

- TASK-3.2 (`scripts/serve_index.html`, JS): add a small helper function
  directly after the existing `esc` definition (around line 483-484):
  ```js
  const neighbourNote = a => a.n_neighbours === 0
    // Defensive only -- MarketScan's default topk (20, against a
    // ~3,204-symbol universe) means this branch does not fire on a real
    // scan today (verified 2026-05-11: all 19 candidates had 20 leaders).
    // Kept for the one code path that can still produce it: a candidate
    // admitted by a supply edge alone, with zero correlation neighbours
    // (D-85/D-87).
    ? "no neighbours retrieved"
    : (a.n_supporting + a.n_contradicting === 0
        ? `${a.n_neighbours} neighbours checked, none moved unusually`
        : `${a.n_neighbours} neighbours checked`);
  ```
  ("moved unusually" reuses the exact verb the page's own step-3a
  explanation already uses — "checks whether any of them moved unusually in
  the days before" — rather than coining new language. "checked" replaces
  the earlier draft's "found": these neighbours were already known before
  the check ran, so "found" overclaims discovery; "checked" matches step
  3a's own verb, "checks whether any of them moved unusually".)
  In the `done` handler's card template (around line 875-877), change:
  ```js
  <div class="lane"><p class="hl">${a.n_supporting} supporting ·
    ${a.n_contradicting} contradicting · effective evidence
    ${a.effective_evidence}</p>${a.description ? ... : ""}</div>
  ```
  to prepend the clause inside the same `<p class="hl">` (no new paragraph,
  no CSS change needed):
  ```js
  <div class="lane"><p class="hl">${neighbourNote(a)} · ${a.n_supporting} supporting ·
    ${a.n_contradicting} contradicting · effective evidence
    ${a.effective_evidence}</p>${a.description ? ... : ""}</div>
  ```

**Halt condition:** if `tests/test_scripts_import.py` fails after TASK-3.1 —
stop; that means the edit broke `serve.py`'s import or entry-point surface,
the exact failure mode Q-43 exists to catch, and it must be fixed before this
phase is considered done, not worked around.

**What would make this wrong:** if the wording above reads, on an actual
rendered card, as implying more neighbours is better ("20 neighbours
checked" sitting next to a `neutral` pill might invite that reading) — if so,
prefer flatter phrasing over anything that could be misread as a strength
indicator, and say so rather than silently shipping a phrase that drifts
toward D-87's territory. `scripts/capture_showcase.py:413-415` builds an
analogous per-assessment dict for `docs/showcase-trace.json` and is **not**
touched by this plan (out of scope, per the team lead's brief, which named
only `serve.py`/`serve_index.html`) — it will render without `n_neighbours`
until a future change adds it there too; this is a known, deliberate gap, not
an oversight to be "fixed" mid-plan.

Also noted, not acted on: the user has separately redirected the wider UI
toward a scan-then-pick flow (scan a date for movers, choose one as the
leader). This plan's change is independent of that and should land either
way, but the wording above names no assumption about the current scan-first
card section or layout being permanent — it is three short clauses attached
to numbers already on the card, and should be re-attached wherever that card
ends up rather than tied to today's section structure.

## Halt conditions (global)
- Any phase whose green step requires relaxing a PHASE-1/2 test rather than
  fixing the implementation — stop, the test was probably right.
- Any design that sums, divides, or otherwise arithmetically combines
  `neighbours` with `effective_evidence`, `supporting`, or `contradicting` —
  that is a new `room` under a different name (see PHASE-2's "what would
  make this wrong"); halt and re-read D-87 before proceeding.
- `check_baseline.py --synthetic` reporting anything other than `baseline
  unchanged: 6 rows identical` at any phase boundary.
- `pytest -q`'s passed count landing below the phase's stated target at any
  boundary where it should already have been reached, or any skip appearing
  where the brief states `0 skipped` today.
