# PLAN-2026-09-09-leader-in

**Goal:** Let a user pick a date, see which companies moved by an unusual
amount, choose one as a leader, and be shown its followers (from the supply
graph), the filing sentence and news behind each, and each follower's
honestly-counted historical response to that leader's past moves —
descriptive only, reusing the existing mode-agnostic graph and both ArangoDB
stores rather than building a parallel pipeline.

**Decisions this depends on:** D-16 (point-in-time by construction; supply
edges filtered by `filing_date <= as_of`), D-18/D-19 (a two-sided,
non-originating assessment layer — this feature must not become a third), D-23
(`CandidateSource` is the only seam; no node branches on mode; `MarketScan`'s
real 2026-05-11 run already sweeps 3,204 symbols to 255 movers and traverses
them to 19 candidates in 8.7s — the cost budget this plan's step 1 reuses
rather than re-measures), D-27 (wide universe for neighbourhoods, never the
signal set), D-38 (a silent path needs an explicit outcome, not a plausible
default), D-59/D-60 (intraday lead-lag is null — genuine peers move *with*
the candidate, not before it), D-62 (funds excluded from neighbour selection
*and* from being offered as leaders — `MarketScan.shocked_leaders` already
drops `excluded_symbols` before testing for a shock), D-73/D-74
(customer→supplier daily lead-lag is a stable, underpowered null: pooled
b=+0.0052, MDE 0.0337 against a 0.02 threshold), D-79 (the candidate is the
leader; `laggers_of` walks INBOUND to its suppliers), D-81 (only edges where
the candidate is the *lagger* vote as evidence — supply edges never do),
D-85/D-86/D-87 (the `origin_leader`-gated, non-voting pattern this plan's new
field must follow, and D-87's precedent that a denominator/ranking born from
an assumed pass-through coefficient gets deleted, not relabelled — the "top
N by \|z\|" list in PHASE-1 below is checked against this precedent explicitly,
since it is a sort and could look like the same mistake), D-88 (the supply
graph's contemporaneous co-move is explained by trailing correlation,
residual negative — this plan must not present the graph as predictive), D-89
(a join node's in-edges must land in the same superstep; the safe pattern is
fanning out from `route_on_neighbourhood` alongside
`leader_state`/`vector_retriever`, not from `START`).

**Open questions that could invalidate it:** none block starting. Q-42 (does
the supply graph buy anything over a correlation screen) is relevant
background but explicitly not re-litigated here. Q-38's overlapping-baseline
defect in `leader_state.py` is **not** inherited by this plan's new code
(PHASE-2 and PHASE-3 both use the literal, non-overlapping contract
`standardised_moves` documents, matching `MarketScan`, not `leader_state.py`).

## Revision note (2026-09-09, same day)

The entry point changed after the first draft of this plan, on direct
instruction from the user, relayed with a correction to one factual claim in
the original brief. Both are folded in below rather than left as an
addendum:

1. **The front door is scan-then-pick, not a symbol text box.** Step 1: pick a
   date (default: the most recent session actually in the data — the existing
   `default_as_of()`, not literal today, since a weekend or a data lag would
   otherwise offer an empty or stale date) and see which companies moved by an
   unusual amount. Step 2: click one of those movers; it becomes the leader,
   and the pipeline runs on its followers. A free-text leader box remains as a
   secondary path for someone who already knows what they want, not the
   primary one. This is a better fit for "given a symbol, show me what it
   might move" than requiring the user to already have a symbol in mind.
2. **Step 1 is not new computation.** `MarketScan.shocked_leaders(as_of)`
   already computes exactly this — 255 movers out of 3,204 on the real
   2026-05-11 scan, already surfaced on the page today as the "swept 3,204 →
   255 moved" counter (`scripts/serve.py`'s `emit("start", ...)`'s existing
   `"scan"` block). PHASE-1 below adds one filter and one endpoint around an
   existing computation; it does not add a parallel path.
3. **This resolves the `direction` question the original brief raised**, for
   the primary path: because the leader is chosen from movers the scan itself
   found shocked, its sign is always known, and `direction` derives from it
   exactly as `MarketScan.candidates()` already does. The "quiet day, no
   thesis" case (PHASE-2 below) now applies only to the secondary, free-text
   path — decided and justified there, unchanged from the first draft.
4. **Correction, not a design change:** the original brief said a scan
   candidate could have an empty *price-correlated* neighbourhood. Verified
   false — `topk=20` against a 3,204-symbol universe means every candidate
   gets 20 neighbours (all 19 real candidates on 2026-05-11 had exactly 20);
   what varies is how many of those 20 *moved* (0 of 20 for 11 of the 19
   candidates, per the corrected brief). This plan does not design around an
   empty correlation neighbourhood anywhere — checked against the draft
   below, it never did, so no phase changes here beyond removing the risk of
   that assumption creeping in later.

## Naming and the two things this feature actually is

Read literally, "given a leader, show its followers and their history" is two
different questions wearing one UI:

1. **A live check**: has this leader moved? If so, run the existing verdict
   pipeline on its followers, exactly as `MarketScan` already does for
   universe-discovered leaders, scoped to one chosen leader instead of a
   sweep. This produces the familiar corroborated/contradicted/neutral pill —
   and inherits, unchanged, the page's existing disclosure that this pill
   measures the follower's *own* correlated neighbourhood, not the supply
   link (D-81), and that the correlation vote alone is worth −4.6bp (README).
   **Now that the primary path only ever offers already-shocked movers to
   click, this case is satisfied by construction** — the direction/quiet-day
   question the first draft treated as central to the primary path turns out
   to matter only for the fallback below.
2. **A standing record**: regardless of whether the leader has moved *today*,
   what has happened, historically, the times it *has* moved by a lot? This
   needs no live shock and is available on any date, for any leader —
   including one entered through the fallback box on a quiet day.

**Class name:** `LeaderFollowers` (`src/lagmatrix/adapters/candidates.py`) —
the step-2 engine, used identically whether the leader arrived via the scan
list or the fallback box. **Track-record module:**
`src/lagmatrix/track_record.py`, one pure function, `historical_episodes`, in
the same style as `shocks.standardised_moves` ("kept out of `graph/nodes/`
deliberately... same maths, different scope"). **Step-1 helper:**
`rank_movers_with_followers` (`src/lagmatrix/adapters/candidates.py`,
module-level, not a `CandidateSource` — see PHASE-1).

## Success criteria

- `uv run pytest -q` passes, 0 skipped where `LAGMATRIX_REQUIRE_LIVE=1` and an
  ArangoDB tunnel is present; live-dependent new tests skip cleanly (or fail
  loudly under `LAGMATRIX_REQUIRE_LIVE=1`, Q-43) otherwise.
- `uv run ruff check .` is clean.
- `uv run python scripts/check_baseline.py --synthetic` prints `baseline
  unchanged: 6 rows identical` after every phase (PHASE-2 through PHASE-4
  touch code the baseline exercises; PHASE-1, PHASE-5, PHASE-6 do not).
- A user can: pick a date (defaulted for them), see a ranked list of
  companies that moved by an unusual amount on that date **and have at least
  one disclosed supplier**, click one, and see that leader's followers with
  their filing sentences, each follower's own recent move, its news, and its
  historical track record stated with an explicit `n` — with no p-value, hit
  rate, expected move, ranking-as-prediction, or score anywhere on that card.
- A user can also type a symbol directly (the fallback path) and get the same
  card layout; on a date that symbol has not moved by `>= sigma`, the
  live-verdict part says so explicitly (no blank "0 candidates" render),
  while the follower list, filing sentences, news and track record are still
  shown.

## PHASE-1 — Step 1: which movers are worth offering as a leader

**Completion criterion:** `uv run pytest tests/test_rank_movers.py -q` all
green (pure-function tests, no live database needed);
`uv run pytest tests/test_arango_topology.py -q` still green after the new
method is added (live-db, skips without a tunnel per Q-43);
`uv run ruff check src/lagmatrix/adapters/candidates.py
src/lagmatrix/adapters/arango.py`.

**Design, decided here:**

- **Bound the list by "has a disclosed follower," not by a bare top-N cutoff
  alone.** A bare top-N by `|z|` would routinely offer a mover with zero
  suppliers in the graph — D-72 already measured that only 8 of 24 alert
  tickers were reachable as customers at all, and the graph today is 514
  vertices / 818 edges against a 3,204-symbol universe, so most daily movers
  are not a customer node in `supplies_to` at all. Offering those anyway
  means the user's first click, most of the time, lands on an empty page —
  the opposite of "more intuitive." Filtering to movers that already have at
  least one supplier is therefore the more useful bound, as flagged, and it
  is cheap: a mover has a follower **iff it is a `_to` vertex of some**
  `supplies_to` **edge**, a plain 1-hop membership test, not a traversal —
  `laggers_of`'s multi-hop machinery is not needed to answer "does this
  mover have any follower at all."
- **One new AQL method, not N calls to `laggers_of`.** Checking "has a
  follower" by calling `laggers_of` once per candidate mover (up to 255 of
  them) would mean up to 255 round trips to answer a yes/no question that a
  single `DISTINCT` scan over `supplies_to` answers for every symbol at once.
  `ArangoTopology.customers_with_suppliers(as_of) -> frozenset[str]` — one
  query, point-in-time filtered by the same `filing_date <= as_of` guard
  every other query in this module already uses (D-16), returning the set of
  symbols that are the `_to` of at least one qualifying edge.
- **Then filter and cap in Python, not AQL — a pure, directly testable
  function.** `rank_movers_with_followers(shocked: dict[str, float],
  has_followers: frozenset[str], top_n: int = 20) -> list[tuple[str, float]]`:
  keep only symbols in `has_followers`, sort by `abs(z)` descending, take the
  first `top_n`. Kept out of `ArangoTopology` and out of `MarketScan`
  deliberately — it touches neither AQL nor the shock-detection maths, it is
  pure list wrangling, and folding it into either would give one of them a
  second, unrelated responsibility (SOLID: single responsibility).
- **This is a display cap, not a ranking that implies predictive value — the
  distinction that matters against D-87.** D-87 deleted `room` because
  dividing one sigma by another **assumed a pass-through coefficient that
  measurement showed did not exist**, and presented the resulting order as if
  it meant something about what would happen next. Sorting a **directly
  observed, already-realised** quantity (today's own `|z|`) purely to decide
  which of 255 items fit on a screen asserts nothing about the future — it is
  the same kind of `nlargest` already used unremarked in
  `graph_retriever.py`'s correlation top-k. The plan states this distinction
  explicitly so PHASE-6's UI copy can say it too, rather than leaving a reader
  to wonder whether "top movers" is a claim.
- **Excluded symbols match `MarketScan`'s own leader-side exclusion (D-62).**
  `shocked_leaders` already drops `excluded_symbols` before testing for a
  shock; the same `frozenset` (loaded from `data/excluded-etfs.csv`, as
  `load()`'s existing `scan` branch already does) is passed through so the
  step-1 list never offers a leveraged/inverse product the pipeline would not
  have offered as a leader anyway.

```python
# adapters/arango.py
def customers_with_suppliers(self, as_of: date) -> frozenset[str]: ...

# adapters/candidates.py
def rank_movers_with_followers(
    shocked: dict[str, float], has_followers: frozenset[str], top_n: int = 20
) -> list[tuple[str, float]]: ...
```

- TASK-1.1 (test, live-db, mirrors `test_arango_topology.py`'s fixture
  shape): `customers_with_suppliers(as_of)` returns exactly the symbols with
  a qualifying (`filing_date <= as_of`) edge pointing to them, excludes one
  whose only edge is filed *after* `as_of` (D-16 point-in-time), and excludes
  a symbol that only ever appears as a *supplier* (`_from`), never a
  customer (`_to`). **Falsifies if:** a future-filed edge leaks through, or a
  pure-supplier symbol is wrongly included.
- TASK-1.2 (test, pure, no live db): `rank_movers_with_followers` keeps only
  symbols present in `has_followers`, sorts by `abs(z)` descending (mixed
  signs), and truncates to `top_n`. **Falsifies if:** sign is used instead of
  magnitude for ordering, or a filtered-out symbol leaks into the result.
- TASK-1.3 (test, pure): `top_n` larger than the filtered set returns the
  whole filtered set, not padded or erroring; `top_n=0` returns `[]`.
- TASK-1.4 (impl): implement both to pass TASK-1.1–1.3.

**What would make this wrong:** if `customers_with_suppliers` traverses
multiple hops instead of testing direct `_to` membership, it would silently
change what "has a follower" means (a 2-hop-only reachable symbol would count,
even though `laggers_of` at `max_hops=1` — the depth this check should imply
— would not reach it from that mover directly). TASK-1.1's exclusion case for
a pure-supplier symbol is the check that would catch a traversal creeping in
by mistake.

## PHASE-2 — `LeaderFollowers`: a third `CandidateSource`

**Completion criterion:** `uv run pytest tests/test_leader_followers.py -q`
all green; `uv run ruff check src/lagmatrix/adapters/candidates.py`;
`uv run python scripts/check_baseline.py --synthetic` unchanged (this phase
adds a class, it does not touch `ExternalSignals`).

**Design, decided here:**

- **`origin_leader` is always set** to the leader symbol the caller named —
  that is exactly what the field is for (`adapters.candidates` docstring: "the
  shocked leader this candidate was discovered from"). Unlike `MarketScan`,
  where the leader is *discovered* by sweeping, here it is *given* — by
  PHASE-1's click, or by the fallback box — but the field means the same
  thing to every downstream reader (`context_fusion`'s description sentence,
  and PHASE-4's track record), so it needs no new semantics.
- **`direction` is derived from the leader's own move, never user-supplied,
  never widened to `Optional`.** `direction` feeds `context_fusion`'s `want`
  sign-matching (`context_fusion.py:47`), unchanged by every other consumer
  of `Candidate`; widening it to `str | None` would force a new branch into a
  node that today assumes it is always one of two literal strings, for a
  benefit (letting a user assert an un-evidenced thesis) this project's
  D-87-shaped history argues against inventing. **When the leader has not
  moved by `>= sigma` on `as_of`** — which, via PHASE-1's list, only happens
  through the fallback text box, since the scan list only ever offers
  already-shocked leaders — `LeaderFollowers.candidates(as_of)` returns
  `[]`, the same contract `MarketScan.shocked_leaders` already has for any
  leader it does not find shocked. This is not a gap for the fallback path:
  PHASE-3's track record is deliberately *not* gated on this, so the fallback
  box on a quiet day still shows the follower list, filing sentences, news
  and history (PHASE-5/6) — only the live verdict pill is legitimately
  absent, because there is no thesis to check.
- **Mirrors `MarketScan`'s public surface on purpose.** `LeaderFollowers`
  exposes `shocked_leaders(as_of) -> dict[str, float]` (a singleton dict, `{}`
  when quiet) alongside `candidates(as_of)`, so `serve.py`'s existing
  `signal_universe = {c.symbol for c in all_c} | origin_leaders` union (the
  fix D-23's Outcome and Q-37 both required) needs **no new branch** — it
  already duck-types on `shocked_leaders()` (Q-37: "Duck-typed rather than on
  the `CandidateSource` protocol"). This is the reused mechanism that keeps
  the *named leader itself* out of every follower's own correlation pool, per
  D-27/D-73/D-81 — the same protection `MarketScan` needed and got.
- **`origin` gains a third value, `"leader_in"`.** `Candidate.origin`'s
  docstring says it exists "so evaluation can slice on it"; reusing `"scan"`
  for a fundamentally different provenance (universe sweep vs. one chosen
  leader) would corrupt exactly the slicing the field exists for. Verified
  safe: `origin` is typed `str`, not a `Literal`, and `grep -rn '\.origin\b'`
  outside `origin_leader` finds exactly one call site
  (`tests/test_market_scan.py:296`, an unrelated assertion) — no code branches
  on the closed set `{"external", "scan"}`.
- **`laggers_of` is guaranteed non-empty when the leader came from PHASE-1's
  list, by construction** (PHASE-1's `customers_with_suppliers` membership
  test *is* "has at least one direct, 1-hop edge," which is exactly what
  `laggers_of(leader, max_hops>=1, as_of)` would find) — this is stated here
  so `LeaderFollowers` is not written defensively around an empty-traversal
  case that the primary path cannot produce; the fallback path can still
  produce it (an arbitrary typed symbol with no suppliers at all), and that
  case is handled the same way D-27 already handles it everywhere else —
  zero candidates, route to `END`, nothing new to build.
- **Direction sign, traversal, and exclusion all reuse existing code
  unchanged**: `shocks.standardised_moves` for the shock test (non-overlapping
  baseline, the literal contract, matching `MarketScan.shocked_leaders` and
  *not* `leader_state.py`'s overlapping one — Q-38's lesson applied to new
  code rather than repeated), `topology.laggers_of(leader, max_hops, as_of)`
  for the traversal (D-79's INBOUND/customer-is-leader direction), and
  `excluded_symbols` for fund/leveraged-product exclusion (D-62).

```python
class LeaderFollowers:
    def __init__(self, closes, topology, leader, excluded_symbols=frozenset(),
                 trail=60, move_win=3, sigma=2.0, max_hops=2): ...
    def shocked_leaders(self, as_of: date) -> dict[str, float]: ...  # {} or {leader: z}
    def candidates(self, as_of: date) -> list[Candidate]: ...        # [] on a quiet day
```

- TASK-2.1 (test): `shocked_leaders` returns `{leader: z}` with correct sign
  when the named leader's engineered move clears `sigma`, `{}` when it does
  not, mirroring `test_market_scan.py`'s `_shock_fixture` pattern but with one
  named leader rather than a sweep. **Falsifies if:** the sign is wrong, the
  quiet case is non-empty, or a *different* symbol's shock in the same frame
  is picked up (this source must never sweep).
- TASK-2.2 (test): `shocked_leaders` uses the non-overlapping baseline
  (`ti - move_win - trail : ti - move_win`), asserted via the same
  `mock.patch(..., wraps=...)` + `assert_called_once`/`baseline_arg` pattern
  `test_market_scan.py`'s `test_shocked_leaders_baseline_excludes_the_recent_window`
  already uses. **Falsifies if:** the implementation copies
  `leader_state.py`'s overlapping window instead.
- TASK-2.3 (test): `candidates(as_of)` on a shocked leader returns one
  `Candidate` per lagger from a fake `topology.laggers_of`, each with
  `origin="leader_in"`, `origin_leader=<leader>`, `direction` matching the
  leader's shock sign, `as_of` as given. **Falsifies if:** any field is wrong,
  or a lagger in `excluded_symbols` is not dropped (D-62 parity).
- TASK-2.4 (test): `candidates(as_of)` on a quiet leader (the fallback-path
  case) returns `[]` without calling `topology.laggers_of` at all (spy
  assertion — a wasted traversal on a quiet day would be silently expensive,
  and D-38 wants a quiet path to be an explicit, cheap no-op).
- TASK-2.5 (test): point-in-time — `candidates(as_of)` passes `as_of` through
  to `topology.laggers_of` unchanged (spy assertion on the call args), so
  D-16/D-79's per-path `filing_date <= as_of` guard is exercised, not
  bypassed.
- TASK-2.6 (impl): implement `LeaderFollowers` to pass TASK-2.1–2.5.
- TASK-2.7 (docs): one line added to `Candidate.origin`'s docstring in
  `domain/models.py` listing the third value.

**What would make this wrong:** if `LeaderFollowers.candidates` ever
traverses the universe rather than the one named leader (a copy-paste from
`MarketScan.shocked_leaders` that keeps the sweep), it silently becomes a
second `MarketScan` with worse test coverage — TASK-2.1's assertion that an
*unrelated* engineered shock in the same fixture frame is absent from the
result is the falsifying check for exactly this.

## PHASE-3 — `historical_episodes`: the genuinely new analytical piece

**Completion criterion:** `uv run pytest tests/test_track_record.py -q` all
green; `uv run ruff check src/lagmatrix/track_record.py`.

**Design, decided here:**

- **Statistic: episode list only. No aggregate beyond the count.** Per pair
  `(leader, follower)`, the function returns one `Episode` per historical date
  the leader moved `>= sigma` (non-overlapping `move_win`-session windows,
  same construction as `shocks.standardised_moves`), each carrying the
  leader's signed move and the follower's realised move over the following
  `horizon_days` sessions. **Nothing is averaged, ranked, or tested.** This is
  the direct, minimal way to satisfy the non-negotiable constraints: a mean
  return implies "expect this"; a win-rate percentage reads as a hit rate; a
  z-score or CI reads as significance dressed up. A bare list with its own
  length is none of those — a reader counts the ups and downs themselves, sees
  the individual magnitudes, and forms their own impression of how noisy it
  is, which is precisely the "make it legible rather than hiding it"
  instruction. Given D-59/D-60 (intraday: peers move *with*, not before, the
  candidate) and D-73/D-74 (daily: pooled b indistinguishable from zero
  against a declared threshold), **most pairs are expected to show either very
  few episodes or a list with no visible pattern** — this design does not
  hide that outcome behind a summary number, it puts the raw list in front of
  the reader instead.
- **`n` is `len(episodes)`, always computed, always shown** — including zero.
  A pair with 3 episodes shows 3; a pair with 0 shows 0 with an explicit
  sentence (PHASE-6), never an empty, unexplained section (D-38/D-86's
  standing rule against a silent path).
- **Point-in-time, applied to a full history scan rather than one snapshot.**
  An episode's shock window *and* its `horizon_days`-session outcome window
  must both end strictly before the query's `as_of` — the discipline D-16
  already applies everywhere else, extended here because this is the first
  place in the codebase that scans *all* of history rather than one date.
  Concretely: let `ti_asof` be the session index convention every other node
  already uses (`sessions.get_loc(sessions[sessions > str(as_of)][0])`); an
  episode at window-end index `ti` is eligible only while
  `ti + horizon_days <= ti_asof`.
- **Explicit no-result, not a fabricated zero, when follower data is
  missing.** If the follower has no complete price history over an episode's
  outcome window (delisted, IPO'd later, gap), that episode is still counted
  in `n` — the leader's move genuinely happened — but its
  `follower_pct`/`follower_sigma` are `None`, exactly D-86's pattern ("a
  missing candidate move is an explicit no-result, not `room = 1.0`"). The
  alternative (silently dropping the episode) would understate `n` in a way
  the "must be honest about n" requirement forbids; the alternative of
  defaulting to `0.0` would fabricate "no move" where the truth is "unknown."
- **Non-overlapping episode windows.** Stepping by `move_win` (not by 1
  session) avoids one large move being counted as several overlapping
  episodes, which would inflate `n` and double-count the same event — the
  same choice `MarketScan.shocked_leaders`/`standardised_moves` already make
  for a single snapshot, extended here across history. Consecutive episodes'
  *outcome* windows may still overlap each other; this is stated as a known
  property, not corrected, since nothing here computes a variance estimate
  that independence would be required for — it is a list of facts, not a
  test statistic.
- **`follower_sigma`** uses the follower's own trailing baseline standard
  deviation (the same `trail`-session window ending strictly before the
  episode's shock window, scaled by `sqrt(horizon_days)`), so it is
  expressed in the same units (own-volatility sigmas) as every other
  z-score in the codebase — directly comparable across pairs without
  implying anything about magnitude *transfer*, which is exactly the
  assumption D-87 found unfounded when `room` divided one sigma by another.
  This function never divides a follower quantity by a leader quantity.

```python
def historical_episodes(
    closes: pd.DataFrame, leader: str, follower: str, as_of: date,
    trail: int = 60, move_win: int = 3, sigma: float = 2.0,
    horizon_days: int = 5,
) -> list[Episode]: ...
```

New domain model (`domain/models.py`):

```python
class Episode(BaseModel):
    """One historical instance of the leader moving >= sigma, and what the
    follower did afterward. Descriptive only — see PLAN-2026-09-09-leader-in
    'What this does not claim'. `follower_pct`/`follower_sigma` are `None`
    when the follower has no complete price history over the outcome window
    (D-86: an explicit no-result, not a fabricated zero)."""

    date: date              # the episode's shock-window end
    leader_sigma: float     # signed
    follower_pct: float | None
    follower_sigma: float | None
```

- TASK-3.1 (test): a fixture with exactly 2 engineered leader shocks (one up,
  one down) over an otherwise quiet series produces exactly 2 `Episode`s with
  correctly signed `leader_sigma`. **Falsifies if:** `n != 2`, or a sign is
  wrong, or a third spurious episode appears from noise.
- TASK-3.2 (test): the follower's engineered deterministic bump in the
  horizon window after an episode is recovered correctly in `follower_pct`
  and `follower_sigma` (hand-computed expected value asserted with
  `pytest.approx`). **Falsifies if:** the follower window is off by one
  session, or `follower_sigma`'s baseline uses the leader's std instead of
  the follower's own.
- TASK-3.3 (test): an episode whose outcome window would extend to or past
  `as_of` is excluded — the direct look-ahead check, in the style of D-59's
  and D-82's falsifying tests. **Falsifies if:** an episode using data not
  yet known as of the query date is included.
- TASK-3.4 (test): a follower with a gap (`NaN` run) spanning an episode's
  outcome window yields that `Episode` with `follower_pct=None,
  follower_sigma=None`, and it is still counted in `len(result)`. **Falsifies
  if:** the episode is silently dropped (undercounts `n`) or defaults to
  `0.0` (D-86's exact failure mode, reintroduced).
- TASK-3.5 (test): `leader` or `follower` absent from `closes.columns`
  returns `[]`, not a `KeyError`.
- TASK-3.6 (test): non-overlapping windows — an engineered move spanning
  `2 * move_win` consecutive sessions produces exactly 1 episode (from the
  first window), not 2 overlapping ones from a naive per-session scan.
- TASK-3.7 (impl): implement `historical_episodes` to pass TASK-3.1–3.6.

**What would make this wrong:** any code path that computes a mean, a
percentage of episodes matching direction, a p-value, or a "confidence" over
the episode list. `grep -n 'mean\|average\|hit.rate\|p.value\|z\s*=' src/lagmatrix/track_record.py`
should return nothing beyond the sigma normalisation already specified — if
review finds an aggregate, this phase has not met its own design brief
regardless of what the tests say, and must be revised before PHASE-4 builds
on it.

## PHASE-4 — Wire `historical_episodes` to `Assessment`, without voting

**Completion criterion:** `uv run pytest -q` green (full suite);
`uv run python scripts/check_baseline.py --synthetic` prints `baseline
unchanged: 6 rows identical`; `uv run ruff check .`.

**Design, decided here:**

- **A new node, `track_record`, gated on `c.origin_leader` — exactly
  `context_fusion`'s existing `description` gate, not a new mode branch.**
  D-23's rule is "no node branches on mode"; this preserves it literally by
  branching on a *field* (`origin_leader` is set or it is not) that already
  exists for this exact purpose and is already read this way by
  `context_fusion`. The node runs in **every** batch, for every source —
  `ExternalSignals` candidates have no `origin_leader` and the node is a
  cheap no-op for them, verified by the baseline being unchanged. This is
  also why `MarketScan`-sourced cards gain a track-record section as a free,
  deliberate side effect of this phase — `MarketScan` candidates carry
  `origin_leader` too, and the gate does not distinguish sources.
- **Fan-out placement: alongside `leader_state`, in the same `Send` wave from
  `route_on_neighbourhood`.** D-89 established that a join node's in-edges
  must land in the *same* superstep or it fires twice on partial state. The
  existing `leader_state`/`vector_retriever` pair is fanned from the same
  `route_on_neighbourhood` call and is therefore safe; `track_record` is added
  to that same `fan_out` list (unconditionally — it needs no `with_news`-style
  toggle) and gets its own `g.add_edge("track_record", "context_fusion")`, so
  all three in-edges to `context_fusion` complete in one superstep, same as
  today.
- **`context_fusion` itself is untouched.** `track_record_by_key` is written
  by the new node directly; `context_fusion` neither reads nor writes it, and
  does not need to — LangGraph merges every channel write from a superstep,
  not only the join node's own return value. This keeps the well-tested
  evidence-weighting logic (Q-12's cluster discount) at zero diff risk.
- **`Assessment.track_record: list[Episode] | None = None`**, populated by
  `assess()` from `track_record_by_key`, `None` when absent — the same shape
  `description` already has, and for the same reason: a field that can be
  read and displayed but that `assess()`'s verdict computation never touches.
- **Checkpoint serialization**: `Episode` must be added to
  `pipeline/runner.py`'s `_ALLOWED_MODELS` tuple (D-44) — found by inspection,
  not by a failing test, since the failure mode is a silent fallback to
  plain-dict round-tripping rather than a hard error; TASK-4.5 is a docs/impl
  task, verified by a checkpoint-and-resume test.

- TASK-4.1 (test): `compute_track_record` returns `{}` (no channel entry) for
  a candidate with `origin_leader=None` — direct unit test, mirrors
  `test_candidate_description.py`'s `test_description_is_none_for_...`.
- TASK-4.2 (test): `compute_track_record` returns a
  `{candidate_key(c): [...]}` entry populated by `historical_episodes` for a
  candidate with `origin_leader` set, using a fixture with a known episode
  count — assert the exact list, not just non-empty.
- TASK-4.3 (test), end-to-end, modeled directly on
  `test_candidate_description.py`'s `_run_full_chain`: for an
  `origin_leader`-bearing candidate run through
  `retrieve_neighbourhood -> leader_state -> track_record -> fuse_evidence ->
  assess`, `a.track_record` is non-`None` and `a.verdict`/`a.effective_evidence`
  are **byte-identical** to the same fixture run without this phase's changes
  (a literal regression pin, in the style of D-87's
  `effective_evidence == 1.0` pin). **Falsifies if:** track record's presence
  changes the verdict or the effective evidence by even a rounding amount —
  the exact failure mode this phase must foreclose.
- TASK-4.4 (test): a batch of two candidates sharing a symbol-adjacent
  fixture (mirrors `test_fanout.py`'s `test_two_candidates_do_not_cross_attribute`)
  never mixes one candidate's episodes into the other's `track_record_by_key`
  entry.
- TASK-4.5 (impl + test): add `Episode` to `_ALLOWED_MODELS`; a
  checkpoint-write-then-resume test (mirrors existing checkpoint tests in
  `tests/test_checkpointing.py`) round-trips an `Assessment` whose
  `track_record` is non-empty and gets back real `Episode` instances, not
  plain dicts.
- TASK-4.6 (impl): register `track_record` node and edge in `builder.py`,
  add `LagMatrixState.track_record_by_key` (`_merge` reducer, matching
  `evidence_by_key`'s shape), add
  `LagMatrixContext.track_record_horizon_days: int = 5` (D-15's 1-10 day band,
  midpoint — an arbitrary but declared default, not tuned against any
  outcome since nothing here is being tested for significance).

**What would make this wrong:** TASK-4.3 failing is the load-bearing check —
if it fails, this phase has repeated D-84's own mistake (introducing a field
that quietly changes the vote) one release after D-87 removed the last one for
exactly that reason. If `check_baseline.py --synthetic` changes by even one
row, halt: it would mean the node is not actually inert for
`origin_leader=None` candidates, and the cause must be found before
proceeding to PHASE-5, not patched around by re-baselining.

## PHASE-5 — `serve.py` wiring (script exemption: no TDD)

Exemption invoked: this phase is infrastructure wiring in a script excluded
from the collected package (per `tests/test_scripts_import.py`'s own
docstring) — no new decision logic beyond what PHASE-1–4 already tested lives
here, only argument plumbing and reuse of existing query functions.

**Completion criterion:** `uv run pytest tests/test_scripts_import.py -q`
passes; two manual runs against a live ArangoDB tunnel
(`uv run python scripts/serve.py --allow-real`), each recorded in this
phase's verification block per D-37, not merely asserted:
1. `curl 'localhost:8000/movers?as_of=<a date with movers>'` returns a JSON
   list of `{"symbol", "sigma"}` objects, sorted by `abs(sigma)` descending,
   every one of which has a nonzero result from `laggers_of` at `max_hops=1`
   (spot-checked against 2-3 entries).
2. `curl 'localhost:8000/run?source=leader_in&leader=<one from step 1>'`
   produces a `done` SSE event whose `assessments` include a non-empty
   `track_record` for at least one candidate.

**Tasks (reuse, not new queries — item 3 of the original brief):**

- TASK-5.1: new `/movers` endpoint on `Handler.do_GET`, parsing `as_of`
  (defaulting via the existing `default_as_of()`) and an optional `limit`
  (defaulting to PHASE-1's `top_n=20`). Computes `MarketScan(closes,
  ArangoTopology(db), excluded_symbols=excluded).shocked_leaders(as_of)`
  (already-existing computation) and
  `ArangoTopology(db).customers_with_suppliers(as_of)` (PHASE-1), passes both
  to `rank_movers_with_followers`, returns the result as JSON. Synchronous —
  not SSE — since nothing here runs the LangGraph pipeline; matches the
  existing `/config`/`/graph` pattern, not `/run`'s streaming one.
- TASK-5.2: `load()` gains `source == "leader_in"`, requiring `--allow-real`
  and a reachable `db` (mirrors the existing `scan` branch's guards
  verbatim), reading a new `leader` query parameter, constructing
  `LeaderFollowers(closes, ArangoTopology(db), leader=leader_symbol,
  excluded_symbols=excluded)`.
- TASK-5.3: `_run`/`stream()` parse `leader` from the query string and thread
  it to `load()`.
- TASK-5.4: `emit("start", ...)` gains a `"leader_in"` block (mirrors the
  existing `"scan"` block's shape) — `{"leader": ..., "shocked": bool,
  "sigma": z or null}` — so the UI can render the explicit quiet-day message
  for the **fallback path only** (PHASE-6) instead of an unexplained empty
  run.
- TASK-5.5: `emit("done", ...)`'s per-assessment payload gains three fields,
  each reusing an existing query rather than adding one:
  - `track_record`: `[{"date":..., "leader_sigma":..., "follower_pct":...,
    "follower_sigma":...} for e in a.track_record]` or `null`.
  - `news`: read from `snap.values.get("news", {})` keyed by
    `candidate_key(a.candidate)` — the vector retrieval already runs and is
    already stored in state; today `stream()` simply never surfaces it. No
    new `NewsIndex` call.
  - `filing_sentence` / `filing_pct`: looked up by calling the existing
    `neighbourhood(c.origin_leader, c.as_of.isoformat())` and finding the row
    whose `symbol == c.symbol`, reusing `cap.disclosure` and `TRAVERSAL_AQL`
    verbatim rather than writing a parallel AQL query.
- TASK-5.6: `FANNED` gains `"track_record"` so the live timeline chart labels
  its spans instead of leaving them unlabelled.
- TASK-5.7: extend `test_scripts_import.py`'s
  `test_serve_exposes_the_entry_points_the_page_depends_on` tuple with
  `"movers"` (the new endpoint's handler function) and any other new
  module-level helper TASK-5.5 introduces (e.g. a small
  `_filing_sentence(db, leader, follower, as_of)` extracted to avoid
  duplicating the row-matching logic at two call sites) — the one test this
  phase must not silently fall outside of.

**What would make this wrong:** a second AQL query string that duplicates
`TRAVERSAL_AQL`/`cap.disclosure` instead of calling the existing
`neighbourhood()` — the exact "parallel query" the original brief says not to
write, and the kind of duplication D-78/D-82 show this codebase has paid for
before when two copies of the same logic drifted. Separately: if `/movers`
recomputes `shocked_leaders` with a *different* `excluded_symbols` set than
`load()`'s `scan`/`leader_in` branches use, the list a user clicks from could
offer a leader that `LeaderFollowers` then treats as excluded — both call
sites must load `data/excluded-etfs.csv` the same way.

## PHASE-6 — `serve_index.html`: scan-then-pick UI (UI/copy exemption: no TDD)

Exemption invoked: presentation and copy, no behavioural seam to test against
— matching this repo's own precedent for every prior UI-only phase (D-80,
D-83's page copy, D-87's card rewrite).

**Completion criterion:** manual visual check against a live run — a
screenshot or terminal capture (`curl localhost:8000/movers?as_of=...`
piped through the rendered page, or a browser screenshot) showing: a date
picker defaulted to `default_as_of()`, a ranked, clickable list of movers
with their signed `sigma`, a click producing the same SSE run as today's
scan/real sources, and a resulting card with a filing sentence, news, and a
track-record section stating `n` explicitly — recorded as this phase's
verification per D-37.

**Tasks:**

- TASK-6.1: a new "step 1" block above the existing run bar — a date input
  (defaulted from `/config`'s `default_as_of`, matching the existing `#asof`
  pattern) and a "Scan for movers" button hitting `/movers?as_of=...`,
  rendering the result as a clickable list (symbol, signed sigma, no other
  decoration) — explicitly captioned, per PHASE-1's design note, as "sorted by
  the size of the move that already happened, not a prediction of what
  happens next," so the sort is never mistaken for the kind of ranking D-87
  removed.
- TASK-6.2: clicking a mover in that list fills the (retained) leader field,
  sets `source` to `leader_in`, and immediately triggers the same `/run` flow
  the existing Run button uses — one click from "what moved" to "what its
  followers look like," per the user's stated goal.
- TASK-6.3: the free-text leader box (the fallback path) stays available
  below/alongside the scan list, in the page's existing voice, explaining
  what it's for: "already know who you're interested in? type it here
  instead — on a quiet day this still shows history, just no live verdict to
  check."
- TASK-6.4: the quiet-day message — rendered from the `start` event's
  `leader_in` field (PHASE-5 TASK-5.4), fallback-path only — e.g. "AAPL moved
  0.4σ over the last 3 sessions, as of 2026-06-01 — below the 2σ bar this page
  uses, so there is no live thesis to check on this date." Displayed in place
  of an empty `#verdicts` list, never a silent blank.
- TASK-6.5: the card renderer (the block that already prints `a.description`
  as `<p class="hl">`) gains, when present:
  - the filing sentence, quoted, in the same visual treatment the "Every edge
    traces to a sentence in a filing" section already uses for a verified
    sentence (including the existing "unverified — table row or header, not
    quotable" fallback for an unverifiable one).
  - the news list (`a.news`), reusing whatever list markup the two-hop cards
    already use.
  - the track record: **`n` stated first**, as its own line ("7 historical
    moves of this size by AAPL before this date"), followed by the episode
    list (date, leader's signed sigma, follower's signed sigma or "no price
    data" per an episode with `follower_sigma: null`). **If `n == 0`**, an
    explicit sentence — "No qualifying moves by AAPL found before this date"
    — never an empty, unlabelled section (D-38/D-86 applied to the UI, not
    just the data layer).
- TASK-6.6: one paragraph added to the existing "What it decided" narrative
  section, next to the D-87/D-88 disclosures already there, stating plainly
  that the track record is history and not a forecast, in the page's existing
  register (see "What this does not claim" below for the content).

**What would make this wrong:** any rendering of the mover list or the
episode list that sorts by, colours by, or otherwise visually ranks
follower "responsiveness" — that is a score by another visual door, the exact
thing D-87 forbids doing again. The mover list sorts by an already-realised
`|z|` only (a display cap, per PHASE-1's note); episodes render in date order,
nothing else.

## What this does not claim

- **The lead-lag effect this feature is built around has been tested twice
  and returned null both times.** Intraday (D-59/D-60, 1,174,267 minute bars,
  83 candidate-dates): genuine non-fund peers move *with* the candidate at
  minute resolution, not before it — 89.2% of dates peak at lag 0, sign test
  p=1.000. Daily, customer→supplier (D-73 single-chain, D-74 pooled across 18
  chains, 1,978 sessions): pooled b=+0.0052 (z=+0.43), inverse-variance
  pooled slope +0.0000, against a pre-registered 0.02 economic threshold —
  stable, clean (I²=0%), and still underpowered rather than a confirmed zero
  (MDE 0.0337 exceeds the threshold).
- **The supply graph's contemporaneous co-movement is a correlation
  artefact, not demonstrated propagation** (D-88): once trailing correlation
  with the leader is netted out, the residual co-movement is *negative* and
  significant in two independent reconstructions. This feature does not claim
  the graph finds non-obvious movers better than a correlation screen would —
  that question (Q-42) is open and not answered here.
- **Therefore this feature shows relationships and history, not
  predictions.** The live verdict pill, where one appears, measures only the
  follower's own correlated-neighbourhood behaviour (the same mechanism, and
  the same disclosed null, `MarketScan` cards already carry) — it is not a
  claim about the leader→follower link itself, which supply edges are
  explicitly excluded from voting on (D-81). The step-1 mover list is sorted
  by an already-realised move's size purely so it fits on a screen — that
  sort is not, and must not be presented as, a ranking of trade quality.
- **The track record panel is a list of past facts, not a hit rate, not a
  backtest, and not evidence of a repeatable pattern.** No p-value, no mean
  return, no win percentage, no confidence, and no ranking of followers by
  "responsiveness" appears anywhere in this feature. Given D-59/D-60/D-73/D-74,
  the honestly expected reading for most pairs is a short list with no visible
  pattern, or too few episodes to say anything at all — and the design (bare
  episode list, `n` stated first, an explicit sentence at `n=0`) exists
  specifically so that outcome is what a reader actually sees, not something
  a summary statistic quietly papers over.
- **A filing sentence establishes a disclosed commercial relationship, not a
  price relationship.** That a company named another as a customer in a 10-K
  is a fact about their business; it is not evidence that their share prices
  move together, which is the separate question D-73/D-74/D-88 already
  measured.

## Out of scope, stated rather than silently dropped

- **`pipeline/runner.py` is not wired to `LeaderFollowers`.** The brief's ask
  is the live UI; the batch runner is a different caller with its own
  `signal_universe` construction that Q-37 already found buggy once for
  `MarketScan` (a missing `as_of` argument, a redundant second sweep). Wiring
  `LeaderFollowers` into it later must repeat Q-37's fix (union
  `shocked_leaders()` into `signal_universe`) rather than assume PHASE-2's
  duck-typing alone is sufficient — flagged here so it is not rediscovered by
  a second `TypeError`.
- **Q-42 (does the graph beat a correlation screen) is not answered here.**
  This plan's "What this does not claim" states the same limitation Q-42
  already logged; it does not run Q-42's proposed comparison.

## Halt conditions

- If PHASE-2's `origin` grep finds a closed-set check on `Candidate.origin`
  that this plan's read of the code missed, halt and log a new Q rather than
  widening that check to admit `"leader_in"` silently.
- If PHASE-4's TASK-4.3 regression pin fails (track record changes a verdict
  or `effective_evidence`), halt — do not "fix" it by rounding or filtering
  the episode list until the pin passes; find and name the leak first.
- If `check_baseline.py --synthetic` changes at any phase boundary, halt
  before proceeding to the next phase.
- If PHASE-1's `customers_with_suppliers` returns a set so large (or so
  small) that "movers with followers" is not actually a useful filter on real
  data — e.g. if it turns out to match almost every mover, or almost none —
  halt and report the measurement rather than shipping a filter that does not
  do the job it was designed for; this is a real-data question PHASE-1's
  tests (synthetic fixtures) cannot answer, so check it manually against the
  live graph before PHASE-6 ships.
- If a live ArangoDB tunnel is unavailable when a phase's tests need one,
  they use `arango_db_or_skip` (skip, or fail under
  `LAGMATRIX_REQUIRE_LIVE=1`, per Q-43) — PHASE-2 and PHASE-3's tests as
  specified above are pure-`pandas`/fixture tests and need **no** live
  database at all (only PHASE-1's TASK-1.1, PHASE-4's TASK-4.5, and PHASE-5's
  manual verification do), so this should not actually bind; if a task above
  turns out to need a live database where this plan assumed it would not,
  halt and correct the plan rather than quietly loosening the task.
