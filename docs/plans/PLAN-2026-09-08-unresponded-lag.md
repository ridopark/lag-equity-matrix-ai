# PLAN-2026-09-08-unresponded-lag

**Goal:** Judge a `MarketScan` candidate `X` by whether it still has room to
follow the leader `Y` that produced it — corroborating while the gap is open,
contradicting only when `X` has genuinely moved the wrong way, and ranking
(not filtering) by how much of the gap remains when it is not.

**Decisions this depends on:** D-18 (conditioning layer, not a forecast),
D-19 (must be able to say "contradicted"), D-23 (`MarketScan`/`CandidateSource`
seam; its outcome note names exactly this gap), D-27 (neighbourhoods drawn
from the wide universe, never the signal set), D-34 (no calibrated
probability), D-73/D-74 (customer→supplier lead-lag: pooled b=−0.0015 against
a pre-registered 0.02 threshold — a stable null, on the wrong side of zero),
D-79 (candidate is the leader of its suppliers, `laggers_of`), D-81 (only
edges where the candidate is the *lagger* count as ordinary `leader_move`
evidence — this plan adds a different, separately-gated mechanism, not a
bypass of D-81's filter).

**Open questions that could invalidate it:** none block starting. Q-37 and
Q-38 are read and explicitly not reopened (see "Out of scope"). This plan
surfaces three new uncertainties that must be logged via `/spike-log open`
at execution time rather than resolved by guessing here — named inline where
they arise, collected in "New open questions to log" below.

## Revision note

This supersedes the first draft of this plan, which scored `X`'s response as
a boolean (`supports: bool`) folded into the ordinary `corroborated` /
`contradicted` verdict. Two things from the user changed the design before
any phase was built, and neither is a small edit to that draft:

1. **"Already responded" is not "contradicted."** The thesis was that `X`
   would follow `Y`. If `X` already has, the thesis *played out* — the
   opportunity is spent, not refuted. Only `X` moving the *opposite* way is
   genuine disconfirming evidence. The verdict vocabulary needed a real
   decision, not a reuse of the existing three-way string.
2. **The output is a ranking by room remaining, not a pass/fail.** "We don't
   care about candidates that already moved" turned out to mean "moved so
   much that entering now would mean chasing," which is a matter of degree,
   not a threshold — so the natural shape of the output is an ordering, not
   a boolean.

**On the pushback about filtering at `MarketScan` selection time:** agreed,
and adopted without reservation. Filtering already-responded candidates out
before assessment would put the discriminator where nothing downstream can
observe it — exactly the "unverifiable is a design defect" failure this
project's own `CLAUDE.md` names, and exactly what D-19 exists to prevent
(a layer that can only ever look supportive). They stay candidates, get
scored, and sort to the bottom, visibly.

## What already exists (read, not re-derived here)

- `Candidate` (`src/lagmatrix/domain/models.py:49`) already carries
  `origin: str` for provenance — precedent for a scan-only field with a
  default that corroboration mode never sets.
- `MarketScan.candidates()` (`src/lagmatrix/adapters/candidates.py:110`)
  already knows, per lagger, exactly which leader claimed it and today
  discards that leader once the `Candidate` is built.
- `leader_state.py` already special-cases the candidate's own symbol to
  always get a `Shock` regardless of `sigma` (`if abs(z) >= sigma or sym ==
  c.symbol`) — the precedent this plan extends to the originating leader.
- `shocks.standardised_moves` (relied on by both `leader_state.py` and
  `MarketScan`) already returns every move in units of *that symbol's own*
  trailing volatility (`cum / (baseline.std() * sqrt(window))`) — so `z_Y`
  and `z_X` are already on a comparable, unitless scale without any extra
  scaling work. This is the only quantity this plan's ranking uses.
- `context_fusion.py` already computes `cand_z` (candidate's own z) and
  `want` (sign of the thesis) once correlation `movers` exist.
- `assessor.py`'s `mine = [e for e in ev if e.kind != "co_mention"]` accepts
  any evidence kind other than `co_mention`.
- `check_baseline.py --synthetic` today prints `baseline unchanged: 6 rows
  identical`; its `COLUMNS` list does not include any field this plan adds,
  so the new fields cannot appear in that CSV at all — a stronger invariance
  guarantee than "the values happen to match." Full suite today: `98 passed,
  9 skipped`. Both are the "before" numbers every phase is diffed against.
- `scripts/serve.py`'s `stream()` builds the `"done"` SSE payload's
  `"assessments"` list (lines ~208-222) from `snap.values.get("assessments",
  [])`, in the order `assess()` returned them; `scripts/capture_showcase.py`
  builds a structurally identical list (~line 411) for `docs/showcase-trace.json`;
  `scripts/serve_index.html:811-817` renders one `<div class="card">` per
  entry, in payload order, with no sort of its own.

## Two findings about `LagEdge.beta`, recorded and deliberately not used

**Finding A — `beta` carries two incompatible quantities.**
`graph_retriever.py:62` computes `beta = corr * win[leader].std() /
cand.std()` (a return-beta-shaped ratio) for `relation == "correlation"`
edges; `arango.py:63` computes `beta = pct_revenue / 100` (a revenue share)
for `relation == "supplier"` edges. Nothing in either file's neighbourhood
distinguishes them by name — a caller reading `edge.beta` generically would
silently mix a volatility ratio with an accounting ratio.

**Finding B — the correlation-edge formula looks like the reciprocal of the
useful direction, and is unverified either way.** Predicting `X`'s move from
`Y`'s (regressing candidate on leader) is `corr * std(cand) / std(leader)`.
The code computes `corr * std(leader) / std(cand)` — the coefficient for
predicting `Y` from `X`, not `X` from `Y`. `grep -rn "\.beta\b" src/ tests/
scripts/` (checked while writing this plan) shows exactly one reader: a test
asserting the literal supplier-edge value (`test_arango_topology.py:160`,
which reads `pct_revenue`, not the correlation formula). **No production code
consumes `LagEdge.beta` today** — `context_fusion.py` weights by cluster
size, never by beta — so nothing currently depends on which direction is
"correct," and nothing in the code states which direction was intended.

**Decision: this plan does not read `LagEdge.beta` for anything.** The room
measure below (see "The room measure") is built entirely from `z_Y` and
`z_X` — quantities already computed the same way, by the same function, for
both symbols — specifically to avoid Findings A and B rather than adjudicate
them. Both findings are logged via `/spike-log open` as a new open question
at PHASE-3 (see below); fixing either is out of scope here per the team's
explicit instruction, because a fix would move any already-published figure
that used the correlation-edge `beta`, which needs its own measurement the
way Q-38 did.

## Design decisions

**D1 — Carry the relationship as a symbol, computed downstream.**
`Candidate` gets one new optional field, `origin_leader: str | None = None`,
set by `MarketScan.candidates()` to the leader that claimed the lagger
(D-23's existing dedup winner). `ExternalSignals` never sets it. `Y`'s `z` is
not stored on `Candidate` — `leader_state.py` computes it, the one place that
already owns `standardised_moves`, so `z_Y` and `z_X` are guaranteed to be
computed on the same baseline convention as each other (even though that
convention differs from `MarketScan`'s own non-overlapping one — Q-38,
parked, unaffected by this plan). A `LagMatrixContext` field was rejected
(wrong scope — context is run-scoped, this is per-candidate); emitting the
originating edge into `lag_edges` was rejected (it would re-enter
`fuse_evidence`'s existing `leader_move` loop and reproduce the exact
circularity D-23/D-81 exist to prevent, one hop removed, through a different
door).

**D2 — Two decoupled outputs, not one.** This plan produces:
1. **`verdict`** (`assessor.py`'s existing `corroborated` / `contradicted` /
   `neutral`, unchanged machinery) — answers "does the weighted evidence
   support the thesis." A `lag_response` `Evidence` unit feeds this **only**
   in the two cases that genuinely bear on it (open, opposed — see D3).
2. **`origin_status` + `room`** (two new fields on `Assessment`) — answer "how
   much of the anticipated move is still ahead of `X`," entirely separate
   from the vote. A "responded" candidate gets `origin_status="responded"`,
   `room=0.0`, and contributes **no** `Evidence` at all, so it cannot itself
   turn a verdict from something else into `"contradicted"` — it is simply
   uninteresting to enter, which is not the same claim as "refuted."
   This is the direct answer to "a reader should be able to tell them
   apart": the two fields are orthogonal on the same `Assessment`, not one
   overloaded string.

Reusing the existing three-way `verdict` for "already responded" (e.g.
folding it into `"neutral"`) was considered and rejected: `"neutral"`
already means "insufficient weighted evidence, or a tie," and overloading it
with "the thesis already played out, successfully" would make one string
mean two unrelated things a reader cannot distinguish without also reading
`room` anyway — at which point the second field was doing the real work and
the reuse bought nothing.

**D3 — The three-way classification, and what each does to the vote.**
Given `c.origin_leader` resolved to a `Shock` (PHASE-2) and correlation
`cand_z`/`want` already in scope in `fuse_evidence`:

    y_component = shocks[c.origin_leader].sigma * want   # Y's move, signed to the thesis
    x_component = cand_z * want                          # X's move, signed to the thesis

  - `y_component <= 0` → **degenerate, skip entirely** (no `Evidence`,
    `origin_status=None`, `room=None`). This is the Q-38-shaped edge case
    where `leader_state`'s overlapping baseline recomputes `Y`'s z with a
    sign or magnitude that no longer supports the thesis it was selected
    for. Not fixed here (Q-38 stays parked); made an explicit, silent-nothing
    outcome rather than a divide-by-a-non-positive-number crash or a
    misleading ratio.
  - `x_component < 0` (**opposed** — `X` moved against the thesis) →
    `origin_status="opposed"`, `room=None` (not "zero room" — the thesis is
    refuted, not merely spent, so no room figure applies).
    `Evidence(kind="lag_response", symbol=origin_leader, supports=False,
    weight=1.0)` — this is the one case that genuinely contradicts, per the
    user's steer, and it counts toward `verdict` through the ordinary
    `w_con` mechanism.
  - `x_component >= y_component` (**responded** — `X` has moved at least as
    much as `Y`, in the thesis direction) → `origin_status="responded"`,
    `room=0.0`. **No `Evidence` is emitted.** The opportunity is gone, but
    nothing here disagrees with the thesis, so nothing should be able to
    push `verdict` to `"contradicted"` on this basis.
  - otherwise (**open**, `0 <= x_component < y_component`) →
    `origin_status="open"`, `room = round(1 - x_component / y_component, 4)`
    — in `(0, 1]`, `1.0` meaning "hasn't moved yet," approaching `0` as `X`
    catches up. `Evidence(kind="lag_response", symbol=origin_leader,
    supports=True, weight=1.0)` — corroborates, as in the original design.
  Tie-break stated explicitly: `x_component == y_component` lands in
  **responded**, not open-with-zero-room, because at that point entering
  would mean chasing exactly as much as the leader's own move — the user's
  stated reason to not care about it.

**D4 — The room measure: a same-basis ratio, not a forecast, and no
`beta`.** `room` is a ratio of two quantities already computed the same way
(`standardised_moves`, own-volatility-normalised) — "how much of `Y`'s own
move `X` has echoed so far, in each symbol's own sigma units" — which is a
description of what has already happened, not a projection of what `X` will
do next. It deliberately does **not** multiply by any transfer coefficient
(measured or assumed): D-74's pooled slope for exactly this relationship is
`b=−0.0015` against a pre-registered `0.02` threshold — a null, on the wrong
side of zero — so assuming any particular fraction of `Y`'s move should
appear in `X`, whether 1:1 or `beta`-scaled, would be presenting as fact the
one quantity this project measured and did not find. `room` is used only to
**order** candidates against each other ("this one has responded less than
that one"), never displayed or reasoned about as an expected return, a
target price, or a magnitude in bp/%. This is the same reasoning that keeps
`co_mention` at `weight=0.0` — retrievable and displayable, never a vote and
never a number a reader could act on as a prediction.

**D4 addendum — the unsigned alternative, considered and rejected.** A
simpler single formula, `responded = abs(z_X) / abs(z_Y)`, `room = 1 -
responded` (clamped), was also on the table — it needs no sign handling and
is arguably easier to read. Rejected because it is sign-blind: a candidate
that moved 1σ *against* `Y` (opposed) and one that moved 1σ *with* `Y` but
has not caught up (open) both produce `responded = 0.5` under that formula,
so a room-sorted list would place a name whose thesis is refuted in the
middle of the "still has room" ranking rather than at the bottom, distinct
from "responded." That collapses exactly the distinction this plan's D3
exists to keep — "opposed" is evidence against the thesis; "hasn't caught up
yet" is not. D3's `x_component`/`y_component` are the same same-basis,
no-transfer-coefficient quantities the unsigned formula uses (`x_component =
cand_z * want` reduces to signed `z_X` once `want` is factored in), signed
so direction survives into the bucket, not just the magnitude.

**D5 — No node reordering; `assessor.py` needs a small, additive change.**
Unlike the first draft, `assessor.py` is no longer untouched: it must read
two new per-candidate values out of state and set them on `Assessment`
(TASK-4.2 below). `builder.py`'s node order and D-23's topology are
unaffected — this is new data flowing through existing edges, not a new
dependency between nodes.

## Success criteria
1. `Candidate.origin_leader: str | None = None` exists; `MarketScan` sets it
   to the claiming leader; `ExternalSignals` never sets it.
2. `leader_state.py` unconditionally emits a `Shock` for `c.origin_leader`
   when set, regardless of `sigma`.
3. `Assessment` gains `origin_status: str | None = None` (`"open"` |
   `"responded"` | `"opposed"` | `None`) and `room: float | None = None`,
   computed per D3, threaded from `context_fusion` through `assess()`.
4. Four fixtures, run end-to-end (`retrieve_neighbourhood` → `leader_state`
   → `fuse_evidence` → `assess`), produce:
   - `X` unmoved → `origin_status="open"`, `room==1.0`, `verdict=="corroborated"`.
   - `X` partially moved, same direction → `origin_status="open"`,
     `0 < room < 1`, `verdict=="corroborated"`.
   - `X` moved >= `Y`, same direction → `origin_status="responded"`,
     `room==0.0`, **`verdict != "contradicted"`** — the test that pins the
     user's core distinction.
   - `X` moved opposite direction → `origin_status="opposed"`, `room is
     None`, `verdict=="contradicted"`.
5. A pure ranking function orders a list of `Assessment`s: `open` (by `room`
   descending) first, then `responded`, then `opposed` last, `None` (no
   `origin_leader`, e.g. corroboration mode) unchanged/stable.
6. `uv run python scripts/check_baseline.py --synthetic` prints `baseline
   unchanged: 6 rows identical` after every phase.
7. `scripts/serve_index.html` no longer contains the string "exactly one
   input moves the verdict"; scan-mode cards show `origin_status`/`room` and
   are ordered by criterion 5; the page states the propagation magnitude
   tested null (D-74: b=−0.0015 vs a 0.02 threshold) so the ordering reads
   as a heuristic, not a forecast.
8. `uv run python -m pytest -q` passes with at least 98 passed / 9 skipped
   (today's count) plus every test this plan adds, 0 new failures.

## PHASE-1 — `Candidate.origin_leader`, set by `MarketScan`
**Depends on:** D-23, D1.
**Completion criterion:** `uv run python -m pytest -q tests/test_market_scan.py`
passes, at least 13 passed (today: 12 tests) plus the one new test below,
with the two updated equality assertions also passing.

- TASK-1.1 (test, `tdd-red`, `tests/test_market_scan.py`):
  - Add `test_candidates_records_the_claiming_leader_as_origin_leader`:
    reuse `_shock_fixture`, a fake topology mapping `LEADUP -> SUP1`, assert
    `scan.candidates(as_of)[0].origin_leader == "LEADUP"`.
  - Update `test_candidates_emits_one_per_lagger_with_scan_origin`'s expected
    value to include `origin_leader="LEADUP"`.
  - Update `test_candidates_dedups_by_larger_abs_z_leader`'s `expected` to
    include `origin_leader="LEADUP"` (the larger-`|z|` leader wins in both
    edge-map orderings, per the existing docstring).
  - Run and observe: the new test fails with `AttributeError`; the two
    updated tests fail on the equality mismatch. Record both before touching
    production code.
- TASK-1.2 (impl, `tdd-green`, `src/lagmatrix/domain/models.py`,
  `src/lagmatrix/adapters/candidates.py`):
  - Add `origin_leader: str | None = None` to `Candidate`, after `origin`.
  - In `MarketScan.candidates()`, pass `origin_leader=leader` when
    constructing each `Candidate` (confirm the loop variable name against
    current source before editing).
  - Update the class docstring's note that the originating edge is
    discarded — it no longer is.

**Halt condition:** if the winning `leader` bound in `claims[edge.lagger]`
construction is not the same one iterated in the outer loop (e.g. a future
refactor separates traversal from construction), stop and re-derive which
variable is correct — TASK-1.1's new test is the check that would fail first.

## PHASE-2 — `leader_state.py` always resolves the origin leader's shock
**Depends on:** the existing `sym == c.symbol` precedent. Does **not** touch
the overlapping-vs-non-overlapping baseline question (Q-38 stays parked).
**Completion criterion:** `uv run python -m pytest -q tests/test_nodes.py`
passes, at least 15 passed (today's count in that file, +1 new test), and
`check_baseline.py --synthetic` still prints `baseline unchanged: 6 rows
identical`.

- TASK-2.1 (test, `tdd-red`, `tests/test_nodes.py`):
  Add `test_leader_state_always_resolves_the_origin_leader_even_below_sigma`:
  a fixture where `origin_leader="Y"` moves **below** `sigma` over
  `move_win` (e.g. a 0.3σ move against a 2.0 threshold) while candidate
  `X` has `origin_leader="Y"` set. Assert `leader_state(...)["leader_shocks"]`
  contains a `Shock` for `"Y"` regardless. Run and observe it fails today.
- TASK-2.2 (impl, `tdd-green`, `src/lagmatrix/graph/nodes/leader_state.py`):
  - Extend `syms = [s for s in [*leaders, c.symbol] if s in returns.columns]`
    to also include `c.origin_leader` when set and present in
    `returns.columns` (dedup — it may already be present via `leaders`).
  - Extend `if abs(z) >= sigma or sym == c.symbol:` to
    `if abs(z) >= sigma or sym == c.symbol or sym == c.origin_leader:`.

**Halt condition:** if `c.origin_leader` is absent from `returns.columns`,
no `Shock` can be produced for it; `leader_state` must not raise.
`fuse_evidence`'s guard in PHASE-3 (`c.origin_leader in shocks`) is what
turns that into an explicit "no `lag_response` for this candidate" rather
than a crash — covered by TASK-3.1, not left implicit.

## PHASE-3 — `fuse_evidence` classifies open / responded / opposed
**Depends on:** D2, D3, D4; D-81 (must not be disturbed — this is a new,
separately-gated path, not a change to the `leaders = [... if e.lagger ==
c.symbol]` filter).
**Completion criterion:** `uv run python -m pytest -q tests/test_nodes.py`
passes, at least 20 passed (PHASE-2's count + 5 new tests below), and
`check_baseline.py --synthetic` still prints `baseline unchanged: 6 rows
identical`.

- TASK-3.1 (test, `tdd-red`, `tests/test_nodes.py`):
  Five new tests, each building `closes` + a `Candidate` with `origin_leader`
  set, running `retrieve_neighbourhood` → `leader_state` → `fuse_evidence`:
  1. `test_lag_response_open_when_candidate_has_not_moved`: `Y` shocked in
     the candidate's direction, `X` quiet (`x_component == 0`) →
     `room_by_key[key] == 1.0`, `origin_status_by_key[key] == "open"`, one
     `Evidence(kind="lag_response", supports=True, weight=1.0)`.
  2. `test_lag_response_open_with_partial_room_when_candidate_partly_moved`:
     `X` moved partway (`0 < x_component < y_component`) → `0 < room < 1`,
     `origin_status == "open"`, same `Evidence` shape (`supports=True`).
  3. `test_lag_response_responded_emits_no_evidence`: `X` moved
     `>= y_component`, same direction → `origin_status == "responded"`,
     `room == 0.0`, **no** `Evidence` with `kind == "lag_response"` anywhere
     in `evidence`.
  4. `test_lag_response_opposed_when_candidate_moved_the_other_way`: `X`
     moved opposite `want` → `origin_status == "opposed"`, `room is None`,
     one `Evidence(kind="lag_response", supports=False, weight=1.0)`.
  5. `test_lag_response_absent_when_origin_leader_is_unset`: an
     `origin="external"` candidate (no `origin_leader`) → no
     `origin_status`/`room` entry for its key, no `lag_response` evidence —
     the corroboration-mode invariance guard at node level.
  Run now; all five fail (`room_by_key`/`origin_status_by_key`/
  `kind="lag_response"` don't exist yet).
- TASK-3.2 (impl, `tdd-green`, `src/lagmatrix/graph/nodes/context_fusion.py`):
  - Hoist `cand_z`/`want` so they're available whether or not `movers` is
    non-empty (currently computed only inside `if movers:`).
  - After the existing correlation block, compute `y_component`/
    `x_component` and classify per D3; append the `lag_response` `Evidence`
    only for `open`/`opposed`; add its weight to `c_effective` when present.
  - Return two new keyed dicts: `room_by_key: dict[str, float | None]`,
    `origin_status_by_key: dict[str, str | None]`, alongside the existing
    `evidence_by_key`/`effective_evidence_by_key`. Add the corresponding
    keys to `LagMatrixState` (`src/lagmatrix/graph/state.py`) as plain
    (non-reducer) dict fields, matching `effective_evidence_by_key`'s shape.
  - Update `Evidence.kind`'s comment in `domain/models.py` to name
    `lag_response` explicitly.
- TASK-3.3 (doc, not code): log Findings A and B (the `beta` section above)
  via `/spike-log open` as a new open question, and likewise the tie-break
  and degenerate-skip choices in D3 as a decision, at execution time — not
  fabricated into `overall.md` by this plan document itself.

**Halt condition:** if hoisting `cand_z`/`want` changes any existing
correlation-only assertion in `tests/test_nodes.py` or
`tests/test_market_scan.py`'s reentry test, that is a real regression in
`leader_move` evidence — stop and fix the hoist so that branch is
byte-identical, don't relax the affected test.

## PHASE-4 — `assessor.py` carries `origin_status`/`room`; proves the split
**Depends on:** PHASE-3; D2, D5; D-19 (must be able to disagree).
**Completion criterion:** `uv run python -m pytest -q tests/test_nodes.py
tests/test_lag_response.py` passes (new file, at least 6 tests below), and
`check_baseline.py --synthetic` still prints `baseline unchanged: 6 rows
identical`.

- TASK-4.1 (test, `tdd-red`, new file `tests/test_lag_response.py`):
  Four two-column `closes` fixtures (`Y`, `X`), mirroring `test_nodes.py`'s
  `_shocked_closes`/`test_market_scan.py`'s `_reentry_fixture` construction —
  `Y` always shocks in the `move_win` window; `X` is engineered to (a) stay
  quiet, (b) partially move, (c) move `>= Y`'s magnitude same direction, (d)
  move opposite direction. Run each through `retrieve_neighbourhood` →
  `leader_state` → `fuse_evidence` → `assess` and assert, per Success
  Criterion 4:
  1. (a) → `verdict=="corroborated"`, `origin_status=="open"`, `room==1.0`.
  2. (b) → `verdict=="corroborated"`, `origin_status=="open"`,
     `0 < room < 1`.
  3. (c) → **`verdict != "contradicted"`** (the falsifying test: if this
     instead reads `"contradicted"`, the "already responded" state has been
     conflated with "refuted," which is the exact distinction the user
     required kept apart), `origin_status=="responded"`, `room==0.0`.
  4. (d) → `verdict=="contradicted"`, `origin_status=="opposed"`,
     `room is None`.
  5. `test_lag_response_weight_alone_clears_min_effective`: fixture (a) with
     no reachable correlation neighbours (`topk=0` or an empty pool) still
     reaches `verdict != "neutral"` — confirms `weight=1.0` alone clears
     `assessor.MIN_EFFECTIVE = 1.0`.
  6. `test_responded_with_no_other_evidence_is_neutral_not_contradicted`:
     fixture (c) with no reachable correlation neighbours →
     `verdict=="neutral"` (no evidence at all, since `lag_response` emits
     nothing for `responded`) — proves "responded" cannot manufacture a
     verdict on its own in either direction.
  Run now and observe all fail (fields don't exist on `Assessment` yet).
- TASK-4.2 (impl, `tdd-green`, `src/lagmatrix/graph/nodes/assessor.py`,
  `src/lagmatrix/domain/models.py`):
  - Add `origin_status: str | None = None` and `room: float | None = None`
    to `Assessment`.
  - In `assess()`, read `state.get("room_by_key", {})` /
    `state.get("origin_status_by_key", {})` alongside
    `effective_evidence_by_key`, and set the two fields per candidate
    (default `None` when the key is absent, e.g. corroboration mode).
  - Add `rank_by_room(assessments: list[Assessment]) -> list[Assessment]` to
    `assessor.py`: sorted by `(priority, -room_or_0)` where
    `priority = {"open": 0, "responded": 1, "opposed": 2, None: 3}
    [a.origin_status]` and the secondary key is `-(a.room or 0.0)` (so
    `open` candidates sort by descending room; the other buckets are stable
    by whatever order they arrived in, since room is `0.0`/`None` within
    each). This is Success Criterion 5's "pure ranking function," TDD'd here
    because it is the ordering the whole feature exists to produce, not
    incidental script wiring.
  - TASK-4.2a (test, `tdd-red`, same file): `test_rank_by_room_orders_open_
    descending_then_responded_then_opposed_then_none` — four hand-built
    `Assessment`s, one per bucket, asserted into the exact expected order,
    plus two `"open"` assessments with different `room` values asserted
    ordered correctly against each other.

**Halt condition:** if any of TASK-4.1's fixtures (b)/(c)/(d) cannot be
constructed to land cleanly in its intended bucket without also perturbing
correlation-neighbour evidence for unrelated reasons (e.g. a large `X` move
drags in a correlated bloc), reduce to the two-column, no-other-neighbours
shape `_reentry_fixture` already uses successfully, rather than tuning
thresholds until the test passes.

## PHASE-5 — Wire the ranking and rewrite the live UI's claim
**Depends on:** PHASE-1 through PHASE-4. Direct edit, not TDD — thin script
wiring calling an already-tested function, plus UI copy; both exempt per
CLAUDE.md ("one-shot scripts under `scripts/`" and "documentation... with no
behavioural change").
**Completion criterion:**
`grep -c "exactly one input moves the verdict" scripts/serve_index.html`
returns `0`; a manual/scripted check of a synthetic scan run (or a small
ad-hoc script using the synthetic fixtures) shows the `"done"` payload's
`assessments` list ordered per `rank_by_room` when `source == "scan"`; the
replacement paragraph contains the substrings `"−0.0015"` (or `"-0.0015"`,
D-74's measured slope) and `"room"` (or `"responded"`/`"already moved"`) in
the same section.

- TASK-5.1 (`scripts/serve.py`): in `stream()`, when `source == "scan"`, sort
  the list comprehension building `"assessments"` (or its input) through
  `rank_by_room` before serialising; add `"origin_status": a.origin_status,
  "room": a.room` to each dict. `source != "scan"` is unaffected (every
  `Assessment` has `origin_status=None`, so `rank_by_room` on that list is
  the identity permutation — safe to apply unconditionally instead of
  branching, if simpler; state which was chosen).
- TASK-5.2 (`scripts/capture_showcase.py`): same two fields added to its
  structurally identical `assessments` list (~line 411), for
  `docs/showcase-trace.json`. **Not regenerated by this plan** —
  regeneration needs a live ArangoDB tunnel and real bars, per this script's
  existing real-data dependency; note it as a follow-up, the same way other
  real-data captures in this repo are treated (D-52's synthetic/real split).
- TASK-5.3 (`scripts/serve_index.html:326-335`): replace the paragraph
  beginning "What actually decides it — and it is less than the diagram
  suggests." Content requirements: (a) price-correlated-neighbour movement
  still votes, unchanged, still null (README: −4.6 bp, z=−0.72); (b) for a
  scan-discovered candidate, whether *the specific company that discovered
  it* has already moved now also affects the verdict, but only when it moved
  the *wrong* way — corroborating while the gap is open, contradicting only
  on a genuine reversal; (c) a candidate that already moved the *right* way
  is shown separately as "responded," sorted toward the bottom, not treated
  as contradicting evidence — the opportunity is spent, not refuted; (d) the
  ordering by remaining room is a heuristic for where to look, not a
  forecast — the measured transfer coefficient for this exact relationship
  (D-74) is a null (b=−0.0015 against a pre-registered 0.02 threshold), so
  no expected move, target, or bp/% figure is shown, only a same-basis
  comparison between candidates; (e) keep the existing framing for supply
  links (still non-voting, D-81) and news (still `weight=0.0`); (f) keep the
  closing sentence about not inventing confidence.
- TASK-5.4 (`scripts/serve_index.html`, JS rendering ~line 811): render
  `origin_status`/`room` on each card when present (e.g. a small badge —
  "room 0.82" for `open`, "already moved" for `responded`, "moved against"
  for `opposed`), handling `room == null` explicitly rather than printing
  `"null"`.

**Halt condition:** if `rank_by_room` applied unconditionally (not gated on
`source == "scan"`) turns out to reorder anything in `real`/`fires`-sourced
runs (it should not — every such `Assessment.origin_status` is `None`), stop
and gate it explicitly rather than relying on the identity-permutation
argument holding by coincidence.

## New open questions to log (via `/spike-log open`, not fabricated here)
- **Findings A/B on `LagEdge.beta`** (see above): two incompatible meanings
  by `relation`, and the correlation-edge formula looking like the reciprocal
  of "predict candidate from leader," unverified and unused today. Answered
  by tracing every future consumer before trusting the field, or by fixing
  the formula and re-deriving anything that used it — not in this plan.
- **Independence of `lag_response` from correlation evidence.** `Y` is
  excluded from `X`'s own correlation pool (`signal_universe`, Q-37's fix),
  so `Y` itself never double-counts — but a third symbol highly correlated
  with `Y` can still contribute ordinary `leader_move` evidence alongside
  `lag_response`, and the two are not weighted against each other. Mirrors
  Q-12's unresolved general question rather than worsening it; not fixed
  here.

## What would make this uninformative
Every edge type this project has tested came back null: correlation
(README: −4.6 bp, z=−0.72), and this exact customer→supplier relationship,
pooled (D-74: b=−0.0015, z=+0.43, against a 0.02 threshold). This plan does
not run a new statistical test — it builds the machinery to compute and rank
`room`, and proves on synthetic fixtures that the classification can
genuinely disagree with the story that motivated it (PHASE-4's fixture (c)
is exactly the case that would have to read `"contradicted"` for the design
to be quietly tautological, and it does not).

Two distinct ways this could still turn out to be decoration, both worth
naming rather than discovering by surprise later:

- **Almost every scan candidate turns out `"responded"` or `"opposed"`,
  rarely `"open"`.** That is a plausible, reportable finding, not a build
  failure — it would mean the market prices these specific relationships
  within a scan's typical window faster than this feature can catch them
  still open. It says something true about how rarely the gap is open at
  all, which is itself worth knowing.
- **`room` turns out uncorrelated with subsequent returns.** If ranking by
  room does not predict which "open" candidates actually move next, the
  ranking is decoration dressed as insight — ordering candidates by a
  quantity that doesn't matter is worse than not ordering them, because it
  looks like it means something. Finding this out needs a replay: for every
  historical scan-mode candidate with `origin_status=="open"`, correlate
  `room` at `as_of` against the candidate's own forward return over the
  horizon D-15 settled on. That measurement is out of scope for this plan
  (it needs historical scan runs this repo does not yet have committed) but
  is the next piece of work this feature's existence obligates, the same
  way D-73/D-74 were the obligation D-72's edge existing created.

## Halt conditions (global)
- Any phase whose green step requires relaxing a PHASE-1..4 test rather than
  fixing the implementation — stop, the test was probably right.
- Any change to `leader_state.py`'s baseline window (Q-38) — parked
  deliberately; if a phase seems to need it, stop and say so.
- Any change to `builder.py`'s node order — not required by D1-D5; if a
  phase seems to need it, stop, the design has drifted.
- Any use of `LagEdge.beta` to scale, project, or otherwise turn `room` into
  an expected-magnitude figure — this is the one thing D4 exists to prevent;
  if a later refinement asks for it, that is a request for a different,
  calibrated feature and needs its own pre-registration, not a quiet edit
  here.
- `check_baseline.py --synthetic` reporting anything other than `baseline
  unchanged: 6 rows identical` at any phase boundary.
