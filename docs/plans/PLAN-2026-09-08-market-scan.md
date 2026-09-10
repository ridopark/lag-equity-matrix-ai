# PLAN-2026-09-08-market-scan

**Status: done.** `MarketScan` exists in `src/lagmatrix/adapters/candidates.py` and is a gated source in `scripts/serve.py`.

**Goal:** Implement `MarketScan` (`src/lagmatrix/adapters/candidates.py`) —
the deferred scan-first `CandidateSource` — so candidate origination inverts
from "an alert arrives, then the graph looks up its neighbours" to "the
universe is scanned for what actually moved, then the graph verifies a real,
dated, directed filing relationship exists to a lagger" — and wire it into
`scripts/serve.py` as a third, honestly-gated source.

**Decisions this depends on:** D-23 (`CandidateSource` is the only seam; the
stub's docstring is this plan's spec), D-27 (a signal's own tickers are
excluded from every candidate's neighbourhood pool — generalised by PHASE-4
below to scan's *originating leaders*, not just its candidates), D-62
(exclude every fund, both ends of the traversal), D-73 (direction: customer →
supplier, i.e. leader → lagger), D-79 (the candidate is the leader;
`laggers_of` walks INBOUND to its suppliers — reused unchanged), D-81 (only
edges where the candidate is the *lagger* count as `context_fusion`
evidence — necessary but, as PHASE-4 found, not sufficient on its own to keep
scan mode non-circular; see "Why this is not circular" below), D-82 (a
point-in-time guard proven by an executed test, not by a docstring), D-54/
Q-26 (the candidate's own symbol is already unconditionally dropped from its
correlation pool, so a `MarketScan`-discovered candidate cannot self-contradict
— confirmed by reading `graph_retriever.py:51`, not re-tested here).

**Open questions this raises rather than closes:** none of D-23/D-27/D-62/
D-73/D-79/D-81/D-82 are reopened. This plan raises two new ones for
`spike-log` (PHASE-6): the `leader_state.py`/`shocks.py` baseline-window
discrepancy below, and whether `pipeline/runner.py` should ever accept
`MarketScan` as an injected `signals=` source (it currently type-hints
`ExternalSignals | None` and always calls `.candidates(as_of)` with `as_of`
possibly `None`, which `MarketScan`'s required `as_of: date` cannot accept —
out of scope here, flagged for whoever wires scan mode into the batch runner
next, along with the same `signal_universe`-generalisation PHASE-4 makes
here).

**No code edits in this document.** Every snippet below is a specification
for the phase that writes it, not a diff already applied.

---

## Why this can still be uninformative, and why it is worth building anyway

Inverting retrieve-then-verify to scan-then-verify fixes the **circularity**
that produced lag 0 in the intraday test (correlation is symmetric — you
cannot select neighbours by "moves together" and be surprised they move
together). It does not manufacture a **result**. The one directed edge type
this project has (`supplies_to`, customer → supplier) has a stable, clean
null at every scale tested so far: D-73 (single chain, |b|=0.009 against a
0.02 threshold), D-74 (pooled across chains, still null, I²=44%), D-63/D-64
(the shipped feature itself, synthetic and real populations). `MarketScan`
reuses that exact edge type to decide which laggers to surface. If the
underlying relationship carries no signal, scanning for it and verifying it
exists on paper will not create one — `MarketScan` can be built entirely
correctly and still emit candidates that show no more corroboration than
`ExternalSignals` candidates do today.

That would still be worth having, for two reasons distinct from "does it
predict":

1. **It is the only way to observe the pipeline on a population it was never
   built for.** Every real candidate to date is drawn from a 24-ticker
   mega-cap alert feed (D-27's whole reason for existing). `MarketScan`'s
   candidates are suppliers — the small-cap end of the same supply chains —
   which the graph, the ETF exclusion (D-62's 1,022-symbol list was built
   against mega-cap neighbourhoods and has never been exercised from this
   end), and `context_fusion`'s clustering discount have never seen live
   input from. A null here is null on a *different, harder* population, not
   a repeat of D-73's.
2. **It closes D-23 honestly.** D-23's own outcome has stood `pending` since
   2026-09-03 with "the claim that no node needs branching is untested until
   it is written." Whether the graph nodes truly need zero changes to accept
   scan-origin candidates (PHASE-1 through PHASE-3 make no changes to
   `graph/nodes/`; PHASE-4 changes only how `signal_universe` is *populated*
   by the caller, not any node) is itself the falsifiable claim under test —
   independent of whether the candidates it produces corroborate anything.

## Why this is not circular — and a re-entry hole that a review caught

`context_fusion` only scores `leader_move` evidence from edges where
`e.lagger == c.symbol` (D-81). A `MarketScan` candidate `X` is discovered via
`topology.laggers_of(Y, ...)` for some shocked leader `Y` — an edge with
`leader=Y, lagger=X` in `ArangoTopology.laggers_of`'s own convention.
Downstream, when `X` re-enters the graph as a `Candidate`, `graph_retriever`
computes `X`'s *own* neighbourhood fresh: correlation edges (always
`lagger=X` by construction) plus `laggers_of(X, ...)` — `X`'s *own*
suppliers, an edge with `leader=X`, which D-81 excludes from evidence. The
specific supply edge that produced `X` as a candidate is therefore never
re-scored as its own corroboration — that is the disqualification D-81 was
built for, applied one hop further than the case it shipped for (PHASE-4 of
the showcase plan), and it holds.

**It is not the only door back in, though.** `graph_retriever.py:51` drops
exactly three things from a candidate's correlation pool: `signal_universe`,
`excluded_symbols`, and the candidate's own symbol. In scan mode,
`serve.py`'s `signal_universe = {c.symbol for c in all_c}` is the set of
*discovered laggers* (every `X`) — it does not contain the *originating
leaders* (the `Y`s) at all, because nothing currently tells the caller what
they were. If `Y` happens to be among `X`'s top-`k` correlated names —
plausible, not a corner case, since customer/supplier pairs are frequently
price-correlated for reasons that have nothing to do with the filed
relationship — `Y` enters `X`'s correlation neighbourhood, `leader_state`
correctly reports `Y` as shocked (it is shocked; that is the entire reason
`X` exists as a candidate), and `context_fusion` counts that as `leader_move`
evidence with `lagger == X`, which D-81 does **not** filter (that edge's
`leader` is `Y`, a neighbour, not `X` itself). The result: `X` is selected
*because* `Y` moved, and then partly scored on the fact that `Y` moved — the
same circularity the inversion exists to remove, re-entering through an
ordinary correlation edge instead of through the supply edge D-81 already
guards. PHASE-4 closes this by extending the existing D-27 mechanism —
originating leaders join `signal_universe` alongside the candidates
themselves — rather than inventing a second one. Note this leaves the
pre-existing protection intact and unchanged: `serve.py`'s
`signal_universe = {c.symbol for c in all_c}` already keeps every `X` out of
every *other* `X`'s pool today, independent of this fix.

---

## REQ — what `MarketScan` must do

- **REQ-1:** `MarketScan.candidates(self, as_of: date) -> list[Candidate]`
  satisfies `CandidateSource` (`adapters/candidates.py:17-18`) — the plain
  positional signature already declared on the stub, not the widened
  `as_of: date | None = None` that `ExternalSignals` uses. A `Protocol`
  member may be implemented with a *narrower* accepted-input signature than
  declared as long as every call the codebase actually makes still resolves
  (structural typing checks the shape the caller uses, not the full
  signature) — `serve.py`'s new call site (PHASE-5) always passes a concrete
  `date`, so this holds. `pipeline/runner.py`'s call site does not, which is
  exactly the incompatibility flagged above and left open.
- **REQ-2:** the set of "shocked leaders" for `as_of` is computed by
  `shocks.standardised_moves` over every symbol in `closes.columns` except
  `excluded_symbols` (D-62) — swept over the **whole universe**, not a
  neighbourhood, which is the entire point of scan-first. `move_win`/`sigma`
  default to `3`/`2.0`, the same values `LagMatrixContext` defaults to, for
  one stated reason: "shocked" should mean the same thing whether it is
  detected at origination (here) or re-checked downstream
  (`leader_state.py`). They are separate constructor parameters, not read
  from `LagMatrixContext`, because `MarketScan` runs at ingest — before a
  `Runtime`/context exists — exactly the position `ExternalSignals` already
  occupies.
- **REQ-3:** each shocked leader is traversed via
  `topology.laggers_of(leader, max_hops, as_of)` (D-73/D-79's convention,
  reused verbatim — no new adapter method). Laggers in `excluded_symbols` are
  dropped. (D-62 was measured and justified against mega-cap correlation
  neighbourhoods; applying the same list here is a defensive, one-line reuse
  of an already-required parameter, not new machinery — cheap enough that
  "this can't happen on a supply graph" is not worth leaving unguarded, per
  D-60's lesson that funds turn up in graphs nobody expected them in.)
- **REQ-4:** one `Candidate` per unique lagger symbol. If two or more shocked
  leaders reach the same lagger, the leader with the **larger `abs(z)`**
  wins (ties broken by leader symbol, ascending) — the strongest shock is the
  most information-bearing one to attribute the candidate to. Nothing is
  recorded about the leaders that lost the tie-break: `Candidate` gains no
  new field for this (CLAUDE §2 — no speculative field; `ExternalSignals`
  candidates already carry no "why" beyond `origin`). The returned list is
  sorted by lagger symbol, ascending, for deterministic test assertions.
- **REQ-5:** the winning leader's shock sign sets the candidate's direction:
  `direction = "up" if z > 0 else "down"`. Reasoning: this is the same
  inheritance `context_fusion.py:57,64` already encodes (`want = 1.0 if
  c.direction == "up" else -1.0`; `supports = (z * want > 0)`) — "leader
  moved up" being evidence *for* an "up" thesis is an existing, accepted
  convention; `MarketScan` just states that thesis explicitly as the
  candidate's `direction` at the moment the candidate is created, instead of
  leaving it to be inferred later.
- **REQ-6:** a filing dated after `as_of` must not be able to produce a
  candidate — proven by an executed test against a real ArangoDB (D-82's
  lesson: "a test is required, not care"), not inferred from the fact that
  `laggers_of` already has this guard.
- **REQ-7:** `MarketScan` exposes the shocked leaders it identified for a
  given `as_of`, so a caller can exclude them from every candidate's
  correlation pool (closes the re-entry hole above; D-27 generalised).
- **REQ-8:** `scripts/serve.py` offers `scan` as a third source, gated on
  **both** `--allow-real` and a reachable ArangoDB (see "ETF/UI" note in
  PHASE-5 for why scan cannot run over the synthetic universe), with
  plain-English copy distinguishing it from the alert-feed source, an honest
  error when selected without both capabilities, and REQ-7's exclusion
  actually wired into `signal_universe`.

---

## Dates: exactly what flows where (REQ-2/REQ-6)

- `as_of: date` — the caller's chosen scan date. Mirrors `Candidate.as_of`'s
  existing meaning: the last **complete, known** session.
- `sessions = closes.index`; `later = sessions[sessions > str(as_of)]`. If
  `later` is empty (no session after `as_of` in the data — `as_of` is the
  most recent bar or beyond it), return `[]`. This is the same idiom
  `graph_retriever.py:35-36` and `leader_state.py:32` already use.
- `ti = sessions.get_loc(later[0])` — the first position *after* `as_of`, so
  `ti - 1` is `as_of`'s own session (the last known one).
- **Deliberately not mirroring `leader_state.py`'s window.**
  `leader_state.py:33-34` sets `baseline = returns.iloc[ti-trail:ti]` and
  `recent = returns.iloc[ti-move_win:ti]` — both windows end at the same row
  (`ti-1`), so `baseline` *contains* `recent` rather than ending strictly
  before it begins, contradicting `shocks.standardised_moves`'s own
  docstring ("baseline must end strictly before returns begins"). No future
  data leaks through this (both windows' last known day is `as_of`), but it
  is not what the function's contract says, and it is not this plan's
  candidate population to fix — `leader_state.py` scores every existing
  candidate today and changing its window changes accepted verdicts, which
  is out of scope (team lead: "no ... altering the verdict logic"). Since
  `MarketScan` is new code with no existing baseline to preserve, it
  satisfies the documented contract literally instead:
  `baseline = returns.iloc[ti - move_win - trail : ti - move_win]`,
  `recent = returns.iloc[ti - move_win : ti]` — baseline's last row
  (`ti-move_win-1`) is strictly before recent's first row (`ti-move_win`).
  Guard: if `ti < move_win + trail`, return `[]` (insufficient history,
  mirrors `graph_retriever.py:41-44`'s guard, extended by `move_win`).
  PHASE-6 logs the discrepancy as a new open question rather than silently
  picking a side.
- `topology.laggers_of(leader, max_hops, as_of)` — the *same* `as_of` the
  caller passed to `MarketScan.candidates`, unmodified, threaded straight
  through (no reformatting, no recomputation) to the adapter whose
  ALL-quantified per-path filing-date guard (`arango.py:29`,
  `_LAGGERS_OF_AQL`) is what actually enforces REQ-6. `MarketScan` does not
  reimplement or duplicate that filter — it delegates and then proves the
  delegation holds (PHASE-3).

---

## Success criteria

Verbatim, checkable. Baseline measured 2026-09-08 at
`0255685e3de5d4e4d53b6beba01f984a3f35f432`: `uv run pytest -q` → **83 passed,
8 skipped**; `uv run ruff check` → **All checks passed!**.

1. `uv run pytest -q` reports **at least 83 passed** plus every test this
   plan adds, **0 failed**, and the pre-existing **8 skipped** unchanged in
   count (they skip on ArangoDB reachability; this plan adds more of that
   same skip-cleanly-if-unreachable kind, not fewer).
2. `uv run ruff check` reports `All checks passed!`.
3. `MarketScan(closes, topology).candidates(as_of)` returns a `list[Candidate]`
   with every element's `origin == "scan"`.
4. A test proves REQ-4's dedup deterministically picks the larger-`|z|`
   leader when two leaders reach one lagger (PHASE-2).
5. A test running against a real ArangoDB (skips cleanly if unreachable,
   per `test_arango_topology.py`'s pattern) proves a lagger reachable only
   through a filing dated **after** `as_of` is absent from the result while
   an otherwise-identical lagger filed **before** `as_of` is present
   (PHASE-3).
6. A test proves that when a candidate's *only* correlated neighbour is its
   own originating leader, excluding that leader via `signal_universe`
   (REQ-7) removes the re-entry evidence path described above, and the
   candidate reaches `END` with no assessment rather than crashing or
   silently scoring itself on its own discovery (PHASE-4).
7. `scripts/serve.py --allow-real` with a reachable ArangoDB, hit at
   `/config`, returns `"graphrag": true`; a subsequent `GET
   /run?source=scan&as_of=2026-05-11&limit=3` (with a tunnel/instance
   present) returns an SSE stream ending in a `done` event, not an `error`
   event, and the `signal_universe` passed into that run's
   `LagMatrixContext` contains both the run's candidate symbols and their
   originating leaders (PHASE-5).
8. `docs/spikes/overall.md`'s D-23 entry has its `Outcome` changed from
   `pending` to a filled-in result citing this plan (PHASE-6).

---

## PHASE-1 — Shock sweep over the universe (REQ-2, REQ-7)

**Routing:** `tdd-red` → `tdd-green`. Behaviour change (new pure logic),
CLAUDE.md's TDD mandate applies in full.
**Depends on:** nothing outside this plan. Uses `shocks.standardised_moves`
unmodified.
**Completion criterion:** `uv run pytest tests/test_market_scan.py -k shock
-q` reports exactly 4 new tests, all passing; `uv run ruff check` clean.

- TASK-1.1 (test, red): create `tests/test_market_scan.py`. Build a `closes`
  fixture with 4 columns over `trail + move_win + 5` business-day sessions
  (`pd.bdate_range("2026-01-01", periods=..., tz="UTC")`), constructed the
  same way `tests/test_nodes.py::_self_edge_closes` and `conftest.py::closes`
  are — small seeded Gaussian noise (`np.random.default_rng(0)`,
  `scale=0.001`) as the baseline for every column, with an engineered jump
  applied to two of them over exactly the last `move_win` sessions: `LEADUP`
  gets `+0.20` added to each of those sessions' log-return (cumulative
  ≈ +20%, several σ above the ~0.001-scale baseline noise regardless of the
  exact seed — do not hand-compute the resulting z; assert against
  `shocks.standardised_moves` called directly on the same windows the
  fixture was built to imply, so the test does not silently drift if the
  windowing constants change), `LEADDOWN` gets `-0.20`. `FLAT` gets no jump.
  `ETF1` gets the identical `+0.20` jump as `LEADUP`. Write
  `test_shocked_leaders_finds_the_engineered_up_and_down_moves` asserting
  the public method (see TASK-1.2 naming below) returns `{"LEADUP": z_up,
  "LEADDOWN": z_down}` with `z_up > 0`, `z_down < 0`, both `abs() >= sigma`,
  and `FLAT` absent. **Falsifies if:** the method misses either engineered
  symbol, or includes `FLAT`.
- TASK-1.2 (test, red): `test_shocked_leaders_excludes_funds_even_when_shocked`
  — same fixture, `excluded_symbols={"ETF1"}` passed to the constructor.
  Assert `ETF1` absent from the result despite moving identically to
  `LEADUP`. **Falsifies if:** `ETF1` appears (D-62 not applied to the
  leader/scan side).
- TASK-1.3 (test, red): `test_shocked_leaders_returns_empty_with_insufficient_history`
  — call with an `as_of` whose `ti < move_win + trail` (e.g. the second
  session in the fixture). Assert `{}`. **Falsifies if:** this raises
  (negative `.iloc` slicing wrapping around) instead of returning empty.
- TASK-1.4 (test, red): `test_shocked_leaders_baseline_excludes_the_recent_window`
  — construct a *second* fixture where the engineered jump sits **only** in
  what would be the overlapping tail of a `leader_state.py`-style baseline
  (i.e., within the last `move_win` sessions before `as_of`) and nowhere
  else; assert the computed sigma is **not** inflated by it — concretely,
  assert the returned `z` for the jumped symbol is large (correctly
  detected) and, separately, assert (via `unittest.mock.patch` on
  `lagmatrix.shocks.standardised_moves`, inspecting the call's `baseline`
  argument) that the baseline actually used spans
  `[ti-move_win-trail : ti-move_win)` and not `[ti-trail : ti)`.
  **Falsifies if:** the windows overlap (i.e. the implementation copied
  `leader_state.py`'s convention instead of REQ-2's stricter one).
- TASK-1.5 (impl, green): implement `MarketScan.__init__` (REQ-1's
  constructor: `closes`, `topology`, `excluded_symbols=frozenset()`,
  `trail=60`, `move_win=3`, `sigma=2.0`, `max_hops=2` — all keyword-capable,
  `closes`/`topology` required positional) and a **public**
  `shocked_leaders(self, as_of: date) -> dict[str, float]` implementing
  exactly the windowing in "Dates: exactly what flows where" above, over
  `[s for s in self.closes.columns if s not in self.excluded_symbols]`.
  Public rather than a private `_`-prefixed helper because it serves two
  callers: `candidates()` internally (PHASE-2), and the wiring layer
  externally (PHASE-4/REQ-7, which needs the exact same leader set to
  exclude from `signal_universe` — one computation, two call sites, no
  divergence possible between "what MarketScan traversed from" and "what
  gets excluded downstream"). `candidates()` itself stays
  `raise NotImplementedError` until PHASE-2.
- TASK-1.6 (verify): run `uv run pytest tests/test_market_scan.py -k shock -q`
  and `uv run ruff check`; paste both outputs verbatim per D-37.

**Halt condition:** if the fixture's seeded noise ever produces a `FLAT` or
baseline-period z that crosses `sigma=2.0` by chance (a real possibility with
Gaussian noise, however small), do not adjust the assertion threshold after
seeing a failure — fix the fixture's noise scale or seed and re-run, and say
so in the verification block, per D-31's stopping-rule discipline against
tuning after looking.

## PHASE-2 — Traversal, dedup, candidate emission (REQ-1, REQ-3, REQ-4, REQ-5)

**Routing:** `tdd-red` → `tdd-green`.
**Depends on:** PHASE-1 (`shocked_leaders`).
**Completion criterion:** `uv run pytest tests/test_market_scan.py -q`
reports all PHASE-1 and PHASE-2 tests passing (target: 9 total in the file);
`uv run ruff check` clean.

- TASK-2.1 (test, red): in `tests/test_market_scan.py`, add a
  `FakeArangoTopology` local to this file (do not import the one from
  `test_nodes.py` — each test file owns its fakes here, matching
  `test_news_node.py`'s `FlakyNewsClient` precedent), duck-typing
  `laggers_of(self, leader: str, max_hops: int, as_of: date) -> list[LagEdge]`
  and recording `(leader, max_hops, as_of)` call tuples, same shape as
  `test_nodes.py`'s version.
  `test_candidates_emits_one_per_lagger_with_scan_origin`: fake returns
  `[LagEdge(leader="LEADUP", lagger="SUP1", ...)]` for `"LEADUP"` and `[]`
  for `"LEADDOWN"`. Using PHASE-1's fixture, assert
  `MarketScan(closes, fake).candidates(as_of) == [Candidate(symbol="SUP1",
  direction="up", as_of=as_of, origin="scan")]` (compare field-by-field, not
  just length). **Falsifies if:** `direction` is wrong, `origin` is not
  `"scan"`, or `SUP1` is missing/duplicated.
- TASK-2.2 (test, red): `test_candidates_reads_max_hops_and_as_of_from_self`
  — construct with `max_hops=3`; assert the fake's recorded calls are
  exactly `[("LEADUP", 3, as_of), ("LEADDOWN", 3, as_of)]` (order: leaders
  sorted by descending `abs(z)`, i.e. `LEADUP`/`LEADDOWN` before any tie —
  use the PHASE-1 fixture's engineered symmetric ±0.20 jump and assert
  exact call order once the fixture's actual z-values are known, printed
  from `shocked_leaders` rather than hand-guessed). **Falsifies if:**
  `max_hops` is hardcoded, or `as_of` passed to `laggers_of` differs from
  the one passed to `candidates`.
- TASK-2.3 (test, red): `test_candidates_dedups_by_larger_abs_z_leader` — two
  leaders both shocked (reuse `LEADUP`, `LEADDOWN`, or add a third engineered
  leader with a smaller `|z|` than `LEADUP`), fake returns the *same* lagger
  `"SHARED"` from both. Assert exactly one `Candidate(symbol="SHARED", ...)`
  in the result, with `direction` matching the **larger-`|z|`** leader's
  sign, and assert this is stable when the fake's internal dict iteration
  order is reversed (construct the fake with leaders inserted in both orders
  across two calls and assert identical output) — proves the dedup key is
  `abs(z)`, not "whichever leader is processed first by accident of dict
  order". **Falsifies if:** two `Candidate("SHARED", ...)` appear, or the
  direction comes from the smaller-`|z|` leader.
- TASK-2.4 (test, red): `test_candidates_drops_excluded_laggers` — fake
  returns a lagger that is also in `excluded_symbols`; assert it is absent
  from the result while a co-returned non-excluded lagger from the same
  leader is present. **Falsifies if:** the excluded lagger leaks through
  (D-62 not applied to the lagger/scan-reached side).
- TASK-2.5 (test, red): `test_candidates_sorted_by_symbol`. Two or more
  laggers returned across leaders; assert the output list's `symbol` values
  are in ascending sorted order regardless of discovery order. **Falsifies
  if:** output order depends on leader/traversal order instead of being
  sorted.
- TASK-2.6 (test, red): `test_candidates_empty_when_nothing_shocked` — a
  `closes` fixture with no engineered jump (or `as_of` chosen where nothing
  crosses `sigma`); assert `candidates()` returns `[]` and the fake's
  `laggers_of` is never called (no wasted traversal when there is nothing to
  traverse from). **Falsifies if:** `laggers_of` is called with zero shocked
  leaders, or a non-empty result appears from nothing.
- TASK-2.7 (impl, green): implement `candidates()`: call
  `self.shocked_leaders(as_of)`, sort items by `(-abs(z), symbol)`, for each
  leader in that order call `self.topology.laggers_of(leader, self.max_hops,
  as_of)`, for each returned edge skip if `edge.lagger in
  self.excluded_symbols`, else claim it in a `dict[str, Candidate]` keyed by
  `edge.lagger` only if not already claimed (first-claim-wins under the
  `(-abs(z), symbol)` ordering **is** the largest-`|z|`-wins rule). Build
  each `Candidate(symbol=edge.lagger, direction=("up" if z > 0 else "down"),
  as_of=as_of, origin="scan")`. Return `sorted(claims.values(), key=lambda
  c: c.symbol)`.
- TASK-2.8 (verify): `uv run pytest tests/test_market_scan.py -q` and `uv run
  ruff check`; paste both outputs verbatim.

**Halt condition:** if TASK-2.2's exact call-order assertion turns out to
depend on floating-point tie-breaking that the fixture cannot guarantee
deterministically, do not loosen the assertion to "set equality" silently —
re-engineer the fixture so the two z-magnitudes are unambiguously different
(e.g. asymmetric jump sizes) and say so in the verification block.

## PHASE-3 — Point-in-time proof against a real filing date (REQ-6)

**Routing:** `tdd-red` → `tdd-green` (expected to require no new production
code if PHASE-2's delegation is correct — see below).
**Depends on:** PHASE-2. Requires a reachable ArangoDB; skips cleanly
otherwise, exactly like `test_arango_topology.py`.
**Completion criterion:** with `LAGMATRIX_ARANGO_URL` tunnelled to a reachable
instance, `uv run pytest tests/test_market_scan.py -k point_in_time -q`
reports the new test passing; without a tunnel, it reports `1 skipped`, not
an error, not a silent pass. `uv run ruff check` clean either way.

- TASK-3.1 (test, red): add `test_candidates_excludes_a_lagger_filed_after_as_of`
  to `tests/test_market_scan.py`, copying `test_arango_topology.py`'s
  `topology` fixture pattern verbatim (module-scoped, disposable database
  named `test_market_scan`, TCP-probe skip, `equity`/`supplies_to`
  collections). Seed one leader `SHOCKLEAD` with two suppliers:
  `EARLYFILED` (`filing_date` well before the chosen `as_of`) and
  `LATEFILED` (`filing_date` well after it). Build a `closes` fixture over
  real business-day sessions (same construction as PHASE-1's, seeded noise
  plus an engineered jump on `SHOCKLEAD` ending exactly at `as_of`). Use the
  **real** `ArangoTopology(db)` (not the fake) as `MarketScan`'s `topology`.
  Assert `EARLYFILED` is present in `MarketScan(closes,
  real_topology).candidates(as_of)` and `LATEFILED` is absent. **Falsifies
  if:** `LATEFILED` appears (a filing dated after `as_of` influenced the
  result — the exact failure mode D-82 found in the vector adapter), or if
  `EARLYFILED` is *also* missing (a false-negative that would hide the real
  bug behind an unrelated wiring error — assert both, not just the absent
  one).
- TASK-3.2 (impl, green — or confirm no change needed): **if TASK-3.1
  fails**, that outcome has exactly one meaning: `MarketScan` is not
  delegating `as_of` to `laggers_of` unmodified — it is doing its own date
  filtering (or none at all) somewhere in between, which is precisely the
  bug D-82 found in the vector adapter, relocated to this adapter. Fix the
  delegation so `as_of` passes through untouched; do not add a second filter
  inside `MarketScan` (the guard belongs in exactly one place, per D-82's
  single-`FILTER` lesson: two independent point-in-time checks on the same
  data is not defense in depth, it is two places that can disagree). **If
  TASK-3.1 passes immediately**, state so explicitly in the verification
  block — this phase is legitimate even when green requires no new code,
  because the thing being proven is that PHASE-2's *existing* delegation
  already satisfies REQ-6, and that was not knowable without running it
  against a real per-path filing-date guard.
- TASK-3.3 (verify): run twice — once with the tunnel up (report the pass),
  once with it torn down (report the clean skip, not absence-of-failure by
  omission).

**Halt condition:** if no ArangoDB is reachable for the entire duration this
phase is executed, it cannot be verified beyond "skips cleanly" — report
exactly that (not MET, not NOT MET: **blocked on infrastructure**, same
status `test_arango_topology.py`'s 8 skips already carry) and say so rather
than treating PHASE-2's unit tests as a substitute for this proof.

## PHASE-4 — Close the leader re-entry hole (REQ-7)

**Routing:** `tdd-red` → `tdd-green`. This closes a real correctness gap
found in review (see "Why this is not circular" above), not a cosmetic
addition — CLAUDE.md's TDD mandate applies.
**Depends on:** PHASE-2 (`candidates()`, `shocked_leaders()` both exist).
Independent of PHASE-3 (does not touch ArangoDB point-in-time behaviour).
**Completion criterion:** `uv run pytest tests/test_market_scan.py -k
"reentry or shocked_leaders_matches"  -q` reports both new tests passing;
`uv run ruff check` clean.

- TASK-4.1 (test, red): `test_shocked_leaders_matches_the_leaders_candidates_actually_queried`
  — using PHASE-2's `FakeArangoTopology` and its call-recording, assert
  `set(scan.shocked_leaders(as_of).keys()) == {call[0] for call in
  fake.calls}` after calling `scan.candidates(as_of)`. This is a consistency
  guard, not a new behaviour: it proves the public introspection surface
  (`shocked_leaders`) reflects exactly the leaders `candidates()` traversed
  from, so a caller building an exclusion set from one cannot silently
  diverge from what the other actually used. **Falsifies if:** the two sets
  differ — e.g. a future change makes `candidates()` filter leaders before
  traversal (say, dropping leaders with no `closes` column) without
  `shocked_leaders()` applying the same filter.
- TASK-4.2 (test, red): `test_excluding_the_originating_leader_removes_the_reentry_evidence_and_the_candidate_reaches_end`.
  Build a small `closes` fixture (reuse `conftest.py`'s `closes` fixture
  shape, or a purpose-built one) where a candidate `X`'s *only* materially
  correlated column is `Y` (its would-be originating leader) — e.g. `Y` plus
  independent noise columns filling out `trail`, `X = 0.8 * Y_returns +
  small noise`, no other column correlated with `X` above the pool's
  ordinary noise floor. Using the `run_graph` fixture (`conftest.py`,
  already used by `tests/test_graph_builder.py`), run
  `run_graph([Candidate(symbol="X", direction="up", as_of=..., origin="scan")],
  signal_universe={"Y"})` — simulating PHASE-5's fix, where `Y` (the
  originating leader) has been unioned into `signal_universe` exactly as
  `X` itself already would be. Assert two things: (a) no `Y`-attributed
  `Evidence` appears anywhere in the run's `evidence`/`evidence_by_key` (the
  re-entry path is closed), and (b) the run produces **no `Assessment`** for
  `X` — `route_on_neighbourhood` sends an empty-neighbourhood branch straight
  to `END` (`graph/builder.py:90,106`) and `assess()` skips any candidate
  key absent from `effective_evidence_by_key` (`assessor.py:26-27`, citing
  D-27) — rather than raising. This is existing, accepted graph behaviour;
  the test's job is to prove *this* exclusion triggers it cleanly, not to
  add a new route. **Falsifies if:** `Y`'s move appears as evidence despite
  being in `signal_universe` (the fix did not close the hole), or the run
  raises/hangs instead of producing zero assessments for `X` (a genuinely
  new hazard the fix introduced, distinct from confirming the pre-existing
  route works).
- TASK-4.3 (impl, green — or confirm no change needed): TASK-4.1 requires no
  new code beyond PHASE-2's `shocked_leaders`/`candidates` already agreeing
  by construction (both iterate the same dict) — if it fails, the two
  methods have diverged and must be made to share the same leader-selection
  logic rather than each recomputing it independently. TASK-4.2 requires no
  change to `graph/nodes/` or `graph/builder.py` at all (confirming D-23's
  "no node needs branching" claim survives this fix too) — the only
  production change this phase can require is in whatever calls
  `MarketScan` and builds `signal_universe`, which is PHASE-5's job, not
  this one's. If TASK-4.2 fails under `run_graph` with `signal_universe`
  already containing `Y`, the defect is inside `graph_retriever`/
  `context_fusion` reading `signal_universe` incorrectly, not inside
  `MarketScan` — flag and stop rather than reach for a `MarketScan`-side
  workaround.
- TASK-4.4 (verify): `uv run pytest tests/test_market_scan.py -q` (full file,
  all four phases so far) and `uv run ruff check`; paste both outputs
  verbatim.

**Halt condition:** if TASK-4.2's fixture cannot be built without `X` also
losing correlation to itself or producing an unrelated spurious neighbour
(i.e. the "only correlate is `Y`" property is hard to guarantee with random
noise at this `trail` length), do not relax the assertion to "evidence count
decreased" — widen the fixture's column count or noise separation until the
exact zero-neighbourhood case is reproducible, and say so in the
verification block, per the same discipline as PHASE-1's halt condition.

## PHASE-5 — Wire `scan` into `scripts/serve.py` and the page (REQ-8)

**Routing:** direct edit, no `tdd-red`/`tdd-green` — this is UI/script
wiring with no new domain behaviour (`serve.py` has no test file today, by
existing convention; `capture_showcase.py`/`capture_trace.py` are the same
kind of exemption). CLAUDE.md's "infrastructure wiring with no behavioural
change" exemption is a stretch here (adding a source *is* new user-facing
behaviour) — routing to direct edit anyway because the alternative, a new
`tests/test_serve.py` driving `ThreadingHTTPServer` over real sockets, would
be the first test of its kind in this file for a feature (`real`) that has
never had one either; matching the file's own established practice rather
than inventing a new testing pattern for this one addition. Verified instead
by the falsifiable `curl`/SSE checks below.
**Depends on:** PHASE-2, PHASE-4 (the `signal_universe` union this phase
wires must use PHASE-4's proven mechanism, not a partial one), and PHASE-3
for full confidence (though PHASE-5 works against the unit-tested path
regardless of ArangoDB reachability at demo time).
**Completion criterion:** the four numbered checks below all produce the
stated output, pasted verbatim.

- TASK-5.1: in `scripts/serve.py`, add `from datetime import date` to the
  import block; extend the existing `from lagmatrix.adapters.candidates
  import ExternalSignals` line to also import `MarketScan`.
- TASK-5.2: move the `db = arango_db()` call in `stream()` to *before* the
  `load(source)` call, and change `load`'s signature to `load(source: str,
  db=None, as_of: str | None = None) -> tuple[pd.DataFrame, list,
  frozenset[str]]` — the third element is "extra symbols to exclude from
  every candidate's neighbourhood, beyond the candidates' own symbols" (REQ-7's
  exclusion set). The existing synthetic/`real` branches each return
  `frozenset()` for it — no change to their behaviour. Pass `db` and `as_of`
  through from `stream()`. Reuse the single `db` value for both `load()` and
  the existing `ArangoTopology(db) if db is not None else None` line that
  builds `ctx.arango_topology` — do not construct a second `ArangoTopology`
  instance.
- TASK-5.3: add a `source == "scan"` branch to `load()`:
  ```python
  if source == "scan":
      if not ALLOW_REAL:
          raise PermissionError("scan needs real market data; start with --allow-real")
      if db is None:
          raise RuntimeError("scan needs ArangoDB; none reachable at " + ARANGO_URL)
      from lagmatrix.adapters.arango import ArangoTopology
      bars = pd.read_parquet("data/bars.parquet")
      closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
      excluded = frozenset(
          ln.split(",")[0] for ln in
          pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln
      )
      scan_date = date.fromisoformat(as_of or "2026-05-11")
      scan = MarketScan(closes, ArangoTopology(db), excluded_symbols=excluded)
      cands = scan.candidates(scan_date)
      origin_leaders = frozenset(scan.shocked_leaders(scan_date))
      return closes, cands, origin_leaders
  ```
  Placed as a third branch alongside the existing `real`/synthetic-default
  branches, preserving both unchanged (each now also returns `frozenset()`
  as the third tuple element). **Note (not fixed here):** the existing
  `real`/synthetic branches construct `LagMatrixContext` in `stream()` with
  no `excluded_symbols` at all — D-62's ETF exclusion is applied to
  `MarketScan`'s own leader/lagger sweep here but was already absent from
  the live demo's correlation neighbourhoods before this plan. Mention,
  don't fix (CLAUDE §3) — out of this plan's scope.
- TASK-5.4: in `stream()`, unpack `closes, all_c, origin_leaders =
  load(source, db, as_of)` and change the existing
  `signal_universe={c.symbol for c in all_c}` line passed to
  `LagMatrixContext` to `signal_universe={c.symbol for c in all_c} |
  origin_leaders` — this is REQ-7's fix landing at its one correct call
  site (PHASE-4 proved the mechanism; this task is where it actually takes
  effect for the live demo). For synthetic/`real`, `origin_leaders` is
  always `frozenset()`, so this line is byte-identical to today's for both
  existing sources.
- TASK-5.5: in `_run`, read a new query param: `as_of_str = (q.get("as_of")
  or ["2026-05-11"])[0]`, and pass it through `stream(source, limit, emit,
  as_of=as_of_str)` (extend `stream`'s signature with a keyword-only
  `as_of: str | None = None`, forwarded to `load`).
- TASK-5.6: in `serve_index.html`'s capability-probe `fetch("/config")`
  handler (around line 418), add a third dynamic `<option>`, gated on
  **both** flags:
  ```js
  if (c.allow_real && c.graphrag) {
    const o = document.createElement("option");
    o.value = "scan"; o.textContent = "scan-first — start from what moved";
    $("#src").appendChild(o);
  }
  ```
  placed after the existing `real` option block. If only one of the two
  flags is true, `scan` is not offered — no partial/broken option appears,
  and no separate error copy is needed for that case since the option simply
  never renders (mirrors how `real` already behaves under `!c.allow_real`).
- TASK-5.7: add a plain-English paragraph after the existing `#realnote`
  paragraph (around line 200), matching that paragraph's register:
  ```html
  <p class="sub" style="margin-top:.5rem" id="scannote"><b style="color:var(--ink2)">Scan-first</b>
    starts from the opposite end. The other two sources hand this page a company that an outside
    alert already flagged, and the graph looks up its neighbours to check the story. Scan-first
    never reads that alert feed at all — it looks at every company's own price history, finds the
    ones that just moved, and only <i>then</i> asks the graph whether a real supplier of that
    mover exists on paper (a company that named it as a customer in an SEC filing, filed before
    today). That ordering matters: the correlation approach at the top of this page picks
    neighbours because they move together, so it is no surprise when they turn out to move
    together — that is what "moves together" means, checked against itself. Scan-first is what
    the graph is normally used for elsewhere in security research: independent evidence of a
    relationship, applied after the fact, not a rule for finding the relationship in the first
    place. It needs both a machine with real price history and a live connection to the supply-chain
    graph, so it only appears in the list above when both are available.</p>
  ```
  Exact wording is this plan's specification, not a placeholder — TASK-5.7
  is "add this text", not "write something explaining scan mode" (per
  "no interpretation" for the executing agent), but minor copy-editing for
  flow is fine as long as the two substantive claims survive: (a) it does
  not read the alert feed, (b) the graph is used to verify a filed
  relationship rather than to pick neighbours by co-movement.
- TASK-5.8 (verify): with a synthetic-only server (`uv run python
  scripts/serve.py`, no `--allow-real`, no ArangoDB tunnel):
  `curl -s localhost:8000/config` → `{"allow_real": false, "graphrag":
  false, ...}` and the page's `<select id="src">` contains no `scan`
  `<option>` (grep the served `/` HTML or check via a browser — state which
  was used). With `--allow-real` and a tunnel present: `curl -s
  localhost:8000/config` → `"allow_real": true, "graphrag": true`, then
  `curl -sN "localhost:8000/run?source=scan&as_of=2026-05-11&limit=3"` ends
  in an SSE `event: done` block (paste it), not `event: error`. Without the
  tunnel but with `--allow-real`: the same `scan` request produces `event:
  error` with a message naming ArangoDB, not a stack trace or a hang.

**Halt condition:** if `data/bars.parquet` has no session on or immediately
after `2026-05-11` for the symbols actually reachable in the live
`supplies_to` graph (i.e. the demo default date produces zero shocked
leaders and thus an empty-but-successful run), do not silently pick a
different hardcoded date to make the demo look non-empty — report the empty
result as correct behaviour (a null is a valid, informative outcome per the
"Why this can still be uninformative" section) and let the reader change the
`as_of` field themselves.

## PHASE-6 — Close D-23's outcome, and log the new open questions

**Routing:** `spike-log` skill. Pure documentation — exempt from TDD.
**Completion criterion:** `docs/spikes/overall.md`'s D-23 entry's `Outcome`
field no longer reads `pending`; two new `Q-nn` rows exist in the Open
Questions table.

- TASK-6.1: update D-23's `Outcome` — state whether every node needed zero
  changes (PHASE-1/2/3/4 touch only `adapters/candidates.py`; confirm no
  file under `graph/nodes/` or `graph/builder.py` was edited: `git diff
  --stat` against this plan's commits, quoted in the entry) and cite this
  plan's file path. Note explicitly that "no node needed branching" turned
  out to be narrower than "no *caller* needed changes" — `scripts/serve.py`
  did need a change (PHASE-5, the `signal_universe` union) to keep the graph
  correct, which is a caller-side consequence of D-27, not a node-side one.
- TASK-6.2: log a new `Q-nn`: does `leader_state.py`'s overlapping
  baseline/recent window (this plan's "Dates" section) materially change any
  existing verdict versus the strict non-overlapping window `MarketScan`
  uses? Blocks: `leader_state.py`, `shocks.py`'s docstring accuracy.
- TASK-6.3: log a new `Q-nn`: should `pipeline/runner.py`'s `signals:
  ExternalSignals | None` parameter widen to accept `MarketScan`, and if so
  (a) what does `as_of=None` (today's "run every date on file") mean for a
  source that requires a concrete date, and (b) does `run()`'s own
  `signal_universe = {c.symbol for c in signals.candidates()}` (unfiltered
  call, `runner.py:80`) need the same PHASE-4 generalisation this plan gave
  `serve.py` — i.e. would batch-mode scanning reopen the exact re-entry hole
  PHASE-4 closed for the live demo, in a second call site nobody has touched
  yet? Blocks: any future wiring of scan mode into the batch/replay path.
- TASK-6.4 (verify): quote both new table rows and the updated D-23 `Outcome`
  paragraph verbatim.

**Halt condition:** none — this phase only records what the preceding five
already established; it cannot fail independently of them.

---

## Halt conditions (plan-wide)

- Any phase whose test, once observed failing (`tdd-red`), turns out to
  contradict D-27, D-62, D-73, D-79, or D-81 as written (not as paraphrased
  above) — stop and re-read the cited decision rather than adjusting the
  test to match the code.
- PHASE-3 finding that `laggers_of`'s existing per-path filing-date guard
  does **not** hold under a fresh fixture — that would mean D-79's own
  acceptance tests (`test_arango_topology.py`) no longer describe the
  running adapter, which is a defect in `ArangoTopology`, not in
  `MarketScan`, and is out of this plan's authority to fix silently.
- PHASE-4 finding a *second* re-entry path beyond the correlation edge
  identified above (e.g. via co-mention/news evidence, once `vector_index`
  is wired for a scan candidate) — stop and raise it rather than assuming
  the `signal_universe` fix is exhaustive; this plan only checked the
  `leader_move` evidence path because that is the only evidence type
  `MarketScan`'s own traversal touches.
- Discovering, during PHASE-5, that `data/bars.parquet`'s schema no longer
  matches the `pivot_table(index="timestamp", columns="symbol",
  values="close")` call the existing `real` branch already uses — that is a
  pre-existing assumption this plan inherits, not one it introduces; halt
  and report rather than reshaping the scan branch around a different schema
  than `real` uses.
