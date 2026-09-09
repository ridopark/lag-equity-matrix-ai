# PLAN-2026-09-09-remove-room

**Status: done.** `room` and `origin_status` are gone from `src/` and `scripts/`.

**Goal:** Delete `room`, `origin_status`, `rank_by_room` and the `opposed`
`lag_response` `Evidence` unit (D-87), and replace them with a single
non-voting `description` field naming the candidate's own thesis-signed move
and the leader that surfaced it — no threshold, no bucket, no denominator, no
ordering.

**Decisions this depends on:** D-84 (introduced `room`/`origin_status`;
**superseded by D-87**), D-85 (the `fuse_evidence` admit-gate change this plan
replaces with a simpler one), D-86 (the missing-candidate-shock guard —
**kept**, adapted to the `description` mechanism), D-87 (2026-09-09T03:50 —
the measured reason `room`/`origin_status` are unfounded and non-predictive;
this plan is D-87's implementation), D-88 (the supply graph's contemporaneous
effect is explained by trailing correlation — cited in the UI copy as the
reason `room`'s denominator was never a distinct supply-chain quantity, but
**not acted on** beyond that; the graph's broader value is Q-42, out of
scope), D-27 (neighbourhoods drawn from the wide universe — see "The one
design decision" below for how this plan reads it), D-81 (only
`lagger == c.symbol` edges count as `leader_move` evidence — untouched), D-19
(the layer must still be able to say "contradicted" — untouched, since
`description` never votes).

**Open questions that could invalidate it:** none block starting. Q-40 (are
`lag_response` and `leader_move` independent enough to sum) becomes **moot**
once `lag_response` no longer exists — logged as a doc task in PHASE-1, not
resolved by guessing here. Q-42 (does the supply graph buy anything over a
correlation screen) is explicitly out of scope — D-88 says to not act on it
"as a side effect of removing `room`," and this plan doesn't.

## The one design decision this plan must make, and why

D-85 widened `fuse_evidence`'s admit gate so a candidate whose `origin_leader`
resolved to a `Shock` reached fusion even with zero correlation-filtered
`leaders`. On the real 2026-05-11 scan that admitted 11 of 19 candidates that
D-27's original gate (`if not leaders: continue`) had been silently dropping.
With `lag_response` deleted, those 11 have no `Evidence` at all — so the
question is whether they still reach `assess()`.

**Decision: (a) they still reach `assess()`, with `verdict=="neutral"` and the
`description` sentence when the candidate's own move is known.** The gate
becomes `if not leaders and not c.origin_leader: continue` — origin_leader
being *set* is sufficient to admit; whether it *resolved to a shock* (D-85's
literal condition) no longer matters, because nothing downstream reads the
leader's own shock any more (the description only needs the candidate's own
z and the leader's name, both known without it).

**Against D-27:** D-27's concern is neighbourhood *sourcing* for correlation
evidence — never draw it from the narrow signal universe, because that
manufactures confluence by construction. It says nothing about whether a
candidate with zero correlation evidence should be invisible. That drop was
an incidental consequence of where the pre-D-85 gate sat, not a stated
principle. `description` carries no vote and no confluence risk — it is a
factual readout of the candidate's own price, computed the same way for every
candidate regardless of how many correlated neighbours it has. Admitting it
changes nothing about where evidence is sourced from.

**What (b) — reverting to `if not leaders: continue`, dropping back to 8
visible candidates — would cost:** the one thing a scan turns up for these 11
names (a real company whose real leader just shocked) becomes invisible again,
silently, which is the exact "unverifiable is a design defect" failure this
repo's CLAUDE.md names — there would be no way for a reader to tell "nothing
happened here" from "the pipeline chose not to look." D-86's guard (say
nothing, log an error, when the candidate's own move truly is unknown) would
also have no candidate left to apply to in the zero-correlation-neighbour case,
since it would already be dropped before that guard's code ever runs.

**Test that pins it** (PHASE-1, TASK-1.2 test 1):
`test_fuse_evidence_admits_candidate_with_origin_leader_and_no_correlation_leaders`
— a candidate with a D-79-shaped supply edge only (`leader=c.symbol`, so the
`lagger == c.symbol` filter yields `leaders == []`) and `origin_leader` set
still gets an `effective_by_key`/`evidence_by_key` entry (`0.0` / `[]`) after
`fuse_evidence`, and therefore an `Assessment` with `verdict == "neutral"`
after `assess()` (PHASE-2). Falsifies option (a) if the candidate is silently
dropped (`key not in effective_by_key`).

## Success criteria
1. `Assessment` no longer has `origin_status`/`room`; it has
   `description: str | None = None`.
2. `assessor.rank_by_room` no longer exists.
3. `fuse_evidence` never constructs `Evidence(kind="lag_response", ...)`;
   `grep -rn 'lag_response' src/` returns nothing.
4. `context_fusion.py`'s admit gate is
   `if not leaders and not c.origin_leader: continue` — a candidate with
   `origin_leader` set and no `lagger == c.symbol` edges still reaches
   `assess()` (the decision above), with `description` populated when its own
   shock is known, `None` and one `errors` entry when it is not (D-86, kept).
5. `leader_state.py` no longer reads or special-cases `c.origin_leader` —
   PHASE-2 of the superseded plan is reverted, since nothing downstream needs
   the origin leader's own shock any more.
6. `Candidate.origin_leader` is unchanged (kept; PHASE-1 of the superseded
   plan is not reversed).
7. `scripts/serve.py` and `scripts/capture_showcase.py` emit `description`
   instead of `origin_status`/`room`, and neither imports `rank_by_room`.
8. `scripts/serve_index.html` contains no `.room`/`.r-open`/`.r-opposed` CSS,
   no `roomBadge`, and the verdict-section copy states the measured null
   result plainly (checked via the grep in PHASE-4).
9. `uv run python scripts/check_baseline.py --synthetic` prints `baseline
   unchanged: 6 rows identical` after every phase.
10. `uv run python -m pytest -q` passes, `>= 111 passed, 9 skipped`, 0
    failures (today: 118 passed, 9 skipped; PHASE-1 removes 11 and adds 8 in
    `tests/test_nodes.py`; PHASE-2 removes 7 via deleting
    `tests/test_lag_response.py` and adds 3 in the new
    `tests/test_candidate_description.py` — net 118 − 11 + 8 − 7 + 3 = 111).

## PHASE-1 — `leader_state.py` reverted; `context_fusion.py` replaces the classification with `description`
**Depends on:** D-86, D-87; the design decision above.
**Completion criterion:** `uv run python -m pytest -q tests/test_nodes.py`
passes, `23` tests (today: 26, minus 11 deleted, plus 8 new — see TASK-1.1),
`0` failures; `uv run python scripts/check_baseline.py --synthetic` prints
`baseline unchanged: 6 rows identical`.

- TASK-1.1 (test, `tdd-red`, `tests/test_nodes.py`):
  - Note, don't touch: `TRAIL`/`MOVE_WIN`/`SIGMA` at lines 484-486 predate
    this feature (commit `f543a117`) and are already dead code unrelated to
    it — pre-existing, left alone per CLAUDE.md §3.
  - Delete the whole block from the `# --- PHASE-2: leader_state always
    resolves the origin leader's shock ------` comment (line 489) through
    end of file (line 972) — 11 tests, 3 helpers
    (`_origin_leader_closes`, `_lag_response_closes`,
    `_run_lag_response_chain`) and the `_candidate_without_own_shock` helper.
  - Add, in their place:
    1. `test_leader_state_ignores_origin_leader_now_that_lag_response_is_
       removed`: reuse `_origin_leader_closes()` verbatim (Y sits at
       `|z|=0.31 < sigma=2.0` and is absent from `lag_edges`). Assert no
       `Shock` with `symbol == "Y"` appears in `leader_shocks` for a
       candidate with `origin_leader="Y"` — the direct regression pin for the
       reversion (falsifies if `leader_state` still force-resolves it).
    2. `test_fuse_evidence_admits_candidate_with_origin_leader_and_no_
       correlation_leaders` (see "the one design decision" above): candidate
       `CAND`, `origin_leader="LEADUP"`, `lag_edges_by_key={key:
       [_supply_edge("SUPPLIER1")]}` (reuse the existing `_supply_edge`
       helper — `leader=CAND`, so `leaders` filtered by `lagger==c.symbol` is
       `[]`), hand-built `leader_shocks={key: [Shock(symbol="CAND",
       pct_change=0.001, sigma=0.1, ...)]}` (candidate's own move known, no
       shock for LEADUP). Assert `key in out["effective_evidence_by_key"]`
       and `out["effective_evidence_by_key"][key] == 0.0` and
       `out["evidence_by_key"][key] == []`. Falsifies if the candidate is
       dropped (option (b) from the design decision).
    3. `test_description_names_the_origin_leader_and_signed_move_toward_the_
       thesis`: reuse the deleted block's `_lag_response_closes`-shaped
       fixture (rename to `_origin_move_closes`, same two-column Y/X data,
       same `trail=10`/`move_win=3`/`sigma=2.0`), `x_recent=[0.0, 0.0, 0.0]`
       (candidate quiet — same fixture as the old "open, unmoved" case, so
       `cand_z == 0.0`, `want=+1` for "up", `x_signed == 0.0 >= 0`). Run the
       real `retrieve_neighbourhood` → `leader_state` → `fuse_evidence`
       chain. Assert `key in out["description_by_key"]`, the string contains
       `"X"`, `"+0.00"`, `"toward"` and `"Y"`. Assert no `Evidence` in
       `out["evidence"]` has `kind == "lag_response"`.
    4. `test_description_signed_against_the_thesis_when_candidate_moved_the_
       wrong_way`: same fixture, `x_recent=[-0.05, -0.06, -0.04]` (the old
       "opposed" case, `x_component == -3.512844`). Assert the description
       contains `"against"` and a negative signed value (`"-3.51"`).
    5. `test_description_absent_when_origin_leader_is_unset`: reuse
       `_shocked_closes()`/`_candidate()` (external, `origin_leader=None`) —
       the existing corroboration-mode invariance fixture. Assert
       `key not in out["description_by_key"]` and no `lag_response` Evidence.
    6. `test_description_skipped_when_candidates_own_shock_is_missing`: reuse
       the deleted block's `_candidate_without_own_shock()` helper verbatim
       (rename if desired) — `origin_leader="Y"` resolves in `shocks`, `X`
       itself does not. Assert `key not in out["description_by_key"]`.
    7. `test_description_missing_candidate_shock_is_reported_in_errors`: same
       fixture as 6. Assert `len(out["errors"]) == 1` and `"X" in
       out["errors"][0]`.
    8. `test_description_never_emits_evidence`: parametrize or duplicate
       assertion across cases 3/4/6 — `not any(e.kind == "lag_response" for e
       in out["evidence"])` in every one. (May be folded into 3/4/6's bodies
       instead of a 9th test, at the implementer's discretion — either way
       every one of 3/4/6 must assert it explicitly, not just by omission.)
  - Run `uv run python -m pytest -q tests/test_nodes.py` and observe: the new
    tests fail (`description_by_key` doesn't exist; the admit gate still
    reads `not (c.origin_leader and c.origin_leader in shocks)`, which
    happens to already satisfy test 2's narrow assertion by accident — verify
    test 2 actually fails for the *right* reason, i.e. that
    `effective_evidence_by_key` construction differs, not that it trivially
    passes; if it passes before any implementation change, tighten it before
    proceeding). Test 1 fails today (`leader_state` still force-resolves Y).

- TASK-1.2 (impl, `tdd-green`, `src/lagmatrix/graph/nodes/leader_state.py`,
  `src/lagmatrix/graph/nodes/context_fusion.py`,
  `src/lagmatrix/domain/models.py`):
  - `leader_state.py`: revert to
    `wanted = [*leaders, c.symbol]` / `syms = [s for s in wanted if s in
    returns.columns]` (drop the `if c.origin_leader and c.origin_leader not
    in wanted: wanted.append(c.origin_leader)` line) and
    `if abs(z) >= sigma or sym == c.symbol:` (drop `or sym ==
    c.origin_leader`).
  - `context_fusion.py`: replace `room_by_key`/`origin_status_by_key`
    initialisation with `description_by_key: dict[str, str] = {}`. Replace
    the admit gate with `if not leaders and not c.origin_leader: continue`.
    Replace the whole `if c.origin_leader and c.origin_leader in shocks:`
    block (the `y_component`/`x_component`/open/responded/opposed
    if/elif/else chain) with:
    ```python
    if c.origin_leader:
        if c.symbol not in shocks:
            errors.append(f"{c.symbol} {c.as_of}: no shock for candidate's own move")
        else:
            x_signed = cand_z * want
            toward = "toward" if x_signed >= 0 else "against"
            description_by_key[key] = (
                f"{c.symbol} has moved {x_signed:+.2f}σ {toward} the thesis; "
                f"surfaced by {c.origin_leader}."
            )
    ```
    No `Evidence` is appended and `c_effective` is not incremented — this
    block never votes. `cand_z`/`want` stay hoisted exactly where they are
    today (unchanged); the pre-existing `leader_move` loop, its `cand_z ...
    else 0.0` fallback, and the `leaders = [... if e.lagger == c.symbol]`
    filter are untouched. Return `"description_by_key": description_by_key`
    in place of the two deleted keys.
  - `domain/models.py`: `Evidence.kind`'s comment drops `"lag_response"`
    from the enumerated list.
  - `src/lagmatrix/graph/state.py`: replace `room_by_key: dict[str, float |
    None]` and `origin_status_by_key: dict[str, str | None]` with
    `description_by_key: dict[str, str]` (plain, non-reducer, matching
    `effective_evidence_by_key`'s existing declaration pattern).
  - `tests/test_market_scan.py:268`'s docstring ("the relationship the later
    `lag_response` evidence (PHASE-3) will check") is now stale — reword to
    reference the `description` mechanism (D-87). Comment-only, no assertion
    changes; folded into this phase since it names the exact mechanism being
    replaced.
  - Doc task (not code): log Q-40 as moot (superseded by `lag_response`'s
    removal) via `/spike-log open`, at execution time — not fabricated into
    `overall.md` by this plan document itself.

**Halt condition:** if any correlation-only (`leader_move`) test result
changes — `test_correlation_only_evidence_is_unchanged_by_the_supply_edge_fix`
or `test_fuse_evidence_effective_evidence_never_exceeds_raw_count` — that is a
real regression in evidence unrelated to this change; stop and find what
touched the shared `cand_z`/`want` hoist rather than relaxing the test.

**What would make this wrong:** if `leader_state.py`'s reversion turns out to
be needed for something other than `lag_response` (e.g. a future consumer
already planned that reads the origin leader's shock) — grep for
`leader_shocks_by_key\[.*origin_leader\]`-shaped reads before deleting; none
exist today (confirmed: `grep -rn 'origin_leader' src/` shows only
`domain/models.py`, `adapters/candidates.py`, and this file, all touched
here). If the admit-gate decision (a) turns out to make the live UI noisy in
practice (many neutral cards with only a description and nothing else), that
is a product judgement to revisit, not a correctness bug — PHASE-4 does not
hide it either way.

## PHASE-2 — `assessor.py` carries `description`; `rank_by_room` deleted
**Depends on:** PHASE-1; D-19 (verdict must still be able to disagree,
untouched by this phase).
**Completion criterion:** `uv run python -m pytest -q tests/test_nodes.py
tests/test_candidate_description.py` passes, `26` tests (PHASE-1's 23 + 3
new), `0` failures; `uv run python scripts/check_baseline.py --synthetic`
prints `baseline unchanged: 6 rows identical`.

- TASK-2.1 (test, `tdd-red`):
  - Delete `tests/test_lag_response.py` entirely (7 tests, all built around
    `origin_status`/`room`/`rank_by_room`, none of which survive).
  - Create `tests/test_candidate_description.py` with 3 tests, reusing this
    file's `_lag_response_closes`/`_run_lag_response_chain`-equivalent
    pattern (rename without "lag_response") but **without** the old file's
    "third trap" workaround (hand-built `OTHERLEAD` edge to avoid `lag_
    response`/`leader_move` double-counting on the same symbol): that trap no
    longer exists, because `description` never emits `Evidence`, so there is
    nothing left for an ordinary `leader_move` unit to collide with. Call
    `retrieve_neighbourhood` for real — this closes the old file's own
    caveat that Success Criterion 4's "end-to-end" was only satisfied from
    `leader_state` onward, not from retrieval.
    1. `test_description_flows_through_to_assessment_without_affecting_
       verdict`: `_origin_move_closes([0.0, 0.0, 0.0])` (candidate quiet, Y
       shocked) run through the full `retrieve_neighbourhood` →
       `leader_state` → `fuse_evidence` → `assess` chain. Assert
       `a.description is not None` and contains `"Y"`; assert `a.verdict ==
       "corroborated"` and `a.effective_evidence == 1.0` — exactly what the
       ordinary `leader_move` unit alone produces (Y is X's only neighbour,
       supports=True, weight=1.0), proving `description` contributed zero to
       the vote.
    2. `test_description_reflects_a_move_against_the_thesis_end_to_end`:
       `_origin_move_closes([-0.05, -0.06, -0.04])`. Assert `a.description`
       contains `"against"`. Do not assert `a.verdict` here (it is driven by
       the ordinary, unrelated `leader_move` unit and is not this test's
       concern — Q-40's now-moot double-counting question no longer applies
       since nothing to collide with exists).
    3. `test_description_is_none_for_corroboration_mode_candidates`: external
       candidate over `_shocked_closes()` (existing fixture), full real
       chain. Assert `a.description is None`.
  - Run `uv run python -m pytest -q tests/test_candidate_description.py` and
    observe all three fail (`Assessment` has no `.description` attribute).

- TASK-2.2 (impl, `tdd-green`, `src/lagmatrix/graph/nodes/assessor.py`,
  `src/lagmatrix/domain/models.py`):
  - `domain/models.py`: remove `origin_status`/`room` from `Assessment`; add
    `description: str | None = None` with a comment naming D-87 and D-86.
  - `assessor.py`: replace `room_by_key`/`origin_status_by_key` reads with
    `description_by_key = state.get("description_by_key", {})`; replace the
    two field assignments in the `Assessment(...)` constructor with
    `description=description_by_key.get(key)`. Delete `rank_by_room` in its
    entirety (the function and its docstring).

**Halt condition:** if `test_lag_response_weight_alone_clears_min_effective`'s
underlying claim (a `lag_response` unit's `weight=1.0` alone clears
`MIN_EFFECTIVE`) has no equivalent to re-pin because `description` carries no
weight at all — that is expected, not a gap: state explicitly in the phase
report that this guarantee is intentionally not carried forward, since
`description` is never evidence.

**What would make this wrong:** if a future reader expects `description` to
influence `effective_evidence` (e.g. because the old `lag_response` weight
did) — TASK-2.1 test 1's `effective_evidence == 1.0` assertion is the
falsifiable pin that it does not; if that assertion is ever loosened to
accommodate a code change, the design has drifted from D-87 and should stop.

## PHASE-3 — Wire `description` through the live server and the showcase capture
**Depends on:** PHASE-1, PHASE-2. Direct edit, not TDD — thin script wiring
reading an already-tested field, exempt per CLAUDE.md ("one-shot scripts
under `scripts/`... with no behavioural change" to the field's own
computation).
**Completion criterion:** `grep -c "rank_by_room\|origin_status\|\"room\"" scripts/serve.py
scripts/capture_showcase.py` returns `0` for each file; `uv run python -m
pytest -q` still passes at PHASE-2's count (no test in this repo currently
exercises `serve.py`'s SSE payload construction directly, so this phase adds
none — confirmed by `grep -rn "assessments" tests/`).

- TASK-3.1 (`scripts/serve.py`): remove the `rank_by_room` import (line 46).
  In `stream()`'s `"done"` payload, iterate `snap.values.get("assessments",
  [])` directly (no ranking — nothing left to rank by) and replace
  `"origin_status": a.origin_status, "room": a.room,` with `"description":
  a.description,`. Remove the now-inapplicable comment about `rank_by_room`
  being safe to apply unconditionally.
- TASK-3.2 (`scripts/capture_showcase.py`): replace `"origin_status":
  a.origin_status, "room": a.room}` (line 415) with `"description":
  a.description}`. Not regenerated by this plan — `docs/showcase-trace.json`
  needs a live ArangoDB tunnel and real bars (confirmed today: it carries no
  `room`/`origin_status` keys at all, so nothing in the committed file is
  stale either way); regeneration stays a follow-up, per D-52's
  synthetic/real split.

**Halt condition:** if any test does exercise `serve.py`'s payload shape
directly (re-check with `grep -rn "origin_status\|\"room\"" tests/` before
editing — today: none), update it in the same commit rather than leaving it
red.

**What would make this wrong:** if `capture_showcase.py`'s real-data run path
is exercised by CI somewhere this plan didn't check (it is not, per
`grep -rn capture_showcase tests/` today) and starts failing on the removed
keys — re-verify that grep before calling this phase done.

## PHASE-4 — Rewrite the live UI's verdict-section copy
**Depends on:** PHASE-1 through PHASE-3; D-87, D-88 (cited, not acted on
beyond the citation — Q-42 stays open). Documentation/CSS/JS with no Python
behavioural change — exempt from TDD per CLAUDE.md.
**Completion criterion:**
`grep -c "\.room\b\|roomBadge\|origin_status\|r-open\|r-opposed" scripts/serve_index.html`
returns `0`; `grep -c "13,063" scripts/serve_index.html` returns `1`;
`grep -c "0.084" scripts/serve_index.html` returns `>= 1`;
`grep -c "\-4.6" scripts/serve_index.html` returns `1` (the pre-existing,
unchanged correlation-null figure, confirming it was not accidentally
deleted alongside the room paragraphs).

- TASK-4.1 (CSS, lines ~117-136): delete the `.room`, `.r-open`, `.r-opposed`
  rules. Change `.card>header span:not(.verdict):not(.room){...}` to
  `.card>header span:not(.verdict){...}` and update its explanatory comment
  (currently references `.v-* / .r-*`; the `.r-*` half no longer exists).
- TASK-4.2 (JS, ~lines 838-854, 861-867): delete the `roomBadge` function and
  its explanatory comment. Drop `${roomBadge(a)}` from the card `<header>`.
  Inside the existing `.lane` `<div>`, after the `n_supporting`/
  `n_contradicting` `<p class="hl">`, add a second one only when present:
  `${a.description ? `<p class="hl">${esc(a.description)}</p>` : ""}` — the
  pre-existing `.hl+.hl{...}` CSS rule already renders a divider between two
  consecutive `.hl` paragraphs, so no new CSS is needed.
- TASK-4.3 (copy, lines ~328-368): keep the opening "What a verdict means"
  paragraph (328-331) and the closing "What still does not vote" paragraph
  (361-368) verbatim. Replace the three paragraphs in between
  ("What actually decides it...", "Already moved is not the same as
  wrong...", "Room is a place to look..."), lines ~332-360, with:
  1. A paragraph stating plainly that exactly one input decides the verdict
     in every mode — price-correlated neighbour movement, still measured
     worthless on its own (−4.6 bp, z=−0.72, unchanged) — and that a scan
     card also carries the `description` sentence, which is displayed like
     the news below and never votes.
  2. A paragraph stating the `room`/`origin_status` mechanism was built,
     measured twice independently (a quant consult over 432 scan dates /
     13,063 supplier-events; a separate rebuild over 30 dates / 209 linked
     events, different data, different code), and removed: the ratio's
     implicit pass-through coefficient came back +0.18 same-day / ~0 forward
     with a null interaction against the leader's own shock size in both
     samples; the forward-return ordering it was built to produce was flat
     in both; and the forward return of `open` names was identical to
     `opposed` names in both samples (+0.084/+0.084; −0.206/−0.206) — the
     three-way split carried no information the plain correlation vote did
     not already have. State, per D-88, that the co-movement the ratio
     assumed is explained by the same trailing price correlation the vote
     above already uses, not a distinct supply-chain transfer effect.
  3. A paragraph describing the replacement as deliberately inert: no
     threshold, no bucket, no ratio, no ranking — a fact about what already
     happened, in each name's own sigma units, naming who surfaced it.

**Halt condition:** if the exact numeric substrings above don't fit the
surrounding prose without sounding like a data dump, adjust phrasing but keep
every cited number — do not summarize the finding away to "it didn't work."

**What would make this wrong:** if this copy is read as a claim that the
supply-graph traversal itself adds nothing (that is Q-42, unresolved) rather
than as a claim about the deleted ratio specifically — reread the drafted
paragraph against D-88's own scope note before publishing, and if it reads
that way, narrow the wording rather than widen the claim to match it.

## Halt conditions (global)
- Any phase whose green step requires relaxing a PHASE-1/2 test rather than
  fixing the implementation — stop, the test was probably right.
- Any change to `leader_state.py`'s baseline window (Q-38) or to
  `graph_retriever.py`/`adapters/arango.py` (D-88/Q-42) — both are explicitly
  out of scope for this reversal; if a phase seems to need either, stop and
  say so rather than drifting into Q-42's territory.
- Any reintroduction of a divide-by-a-leader's-move quantity, under any name
  — that is the exact thing D-87 found unfounded; if a later refinement wants
  it back, it needs its own pre-registration against real forward returns,
  not a quiet re-add here.
- `check_baseline.py --synthetic` reporting anything other than `baseline
  unchanged: 6 rows identical` at any phase boundary.
- `pytest -q`'s passed count landing below Success Criterion 10's `111` at
  any phase boundary where it should have already been reached.
