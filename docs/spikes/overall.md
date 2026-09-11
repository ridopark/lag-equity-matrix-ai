# Spikes & Decisions — Running Log

Research notes, spike results, and the decisions that came out of them. Append as
we go; don't rewrite history — supersede an entry instead.

Every decision carries five things: a one-line **summary** (the heading), the
**decision**, the **why**, the **outcome** once we've lived with it, and a
**timestamp**. Outcome starts as `pending` and gets filled in later — that
feedback loop is what makes the log worth keeping.

Entries D-01…D-10 were backfilled at the end of the session that produced them,
so they carry a date only. Everything from D-11 on carries a full ISO timestamp.

## How to use this folder

- One file per spike: `docs/spikes/<NN>-<short-slug>.md` (e.g. `01-arango-traversal-latency.md`).
- Each spike file: **Question → What we tried → What we measured → Conclusion**.
- Every spike that changes direction gets a decision entry below, linking back.
- A decision that later turns out wrong is not deleted — mark it `Superseded by D-nn`.

---

## Decision Log

### D-01 — LangGraph StateGraph as the pipeline orchestrator
- **When:** 2026-09-03
- **Decision:** Orchestrate the pipeline as a LangGraph `StateGraph`.
- **Why:** Conditional routing plus parallel fan-out/join is exactly the shape of
  shock → (topology ‖ news) → fusion. A hand-rolled asyncio pipeline was the
  alternative; rejected because we would rebuild routing, state merging, and
  checkpointing ourselves.
- **Outcome:** Correct, and now fully exercised. `build_graph()` compiles a
  seven-node graph with `Send` fan-out, a parallel `leader_state`/
  `vector_retriever` join, checkpointing, caching, retry, timeout and an
  `interrupt()` review gate — 31 tests, 98 real candidates, all six D-35 items
  landed. The judgement that a hand-rolled asyncio pipeline would have meant
  rebuilding routing, state merging and checkpointing held up: every one of
  those turned out to be a framework primitive with semantics we had to probe
  rather than assume (spike 12), and reimplementing them would have meant
  discovering the same fourteen behaviours the hard way.
- **Status:** Accepted

### D-02 — ArangoDB for the structured market topology
- **When:** 2026-09-03
- **Decision:** Store equities and leader→lagger edges in ArangoDB.
- **Why:** Native multi-model graph; one AQL traversal returns laggers at N hops.
  Alternative was Postgres with recursive CTEs — workable, but the traversal and
  the edge-weight filtering get ugly past one hop.
- **Outcome:** pending — no schema created yet.
- **Status:** Accepted

### D-03 — Qdrant for the news/earnings vector index
- **When:** 2026-09-03
- **Decision:** Use Qdrant as the vector store behind `adapters/vector.py`.
- **Why:** Docker-simple and has real filtered-search support, which we need —
  the query is never pure similarity, it is similarity *plus* `symbol IN (...)`
  *plus* recency. pgvector was the alternative and would collapse one service,
  but its filtered-search performance at our latency budget is unmeasured.
- **Outcome:** Wrong premise, not wrong benchmark. Never measured — superseded
  before testing, because ArangoDB turned out to cover the vector half itself
  (spike 01), making the two-service split unnecessary rather than merely
  unproven.
- **Status:** Superseded by D-13

### D-04 — langchain-anthropic rather than the raw anthropic SDK
- **When:** 2026-09-03
- **Decision:** Call Claude through `ChatAnthropic` from `langchain-anthropic`.
- **Why:** The whole pipeline is LangGraph; a second message/tool format in the
  analyst node buys nothing. The raw `anthropic` SDK gives finer control over
  caching and thinking config — revisit if the analyst node needs it.
- **Outcome:** pending — `adapters/llm.py` is a stub.
- **Status:** Accepted

### D-05 — Default model claude-opus-5
- **When:** 2026-09-03
- **Decision:** Default `Settings.model` to `claude-opus-5`.
- **Why:** The analyst node is the reasoning-heavy step and correctness matters
  more than token cost while we are still establishing whether the signal exists
  at all. Cheaper models are the obvious tuning lever once latency is measured.
- **Outcome:** Finalised without ever being measured, because the reasons for
  hesitating were removed rather than answered. Q-03 and Q-04 both closed under
  D-15 — a daily cadence makes an expensive model affordable per signal, so the
  latency-versus-quality tradeoff that made this tentative no longer exists.
  Caveat recorded rather than hidden: D-17 moved the LLM's main job from
  per-signal analysis to bulk labelling of co-mention articles, which is a
  different cost profile entirely (thousands of calls, not one per fire). That
  is a new question, not this one — see Q-19.
- **Status:** Accepted

### D-06 — Market feed left provider-agnostic
- **When:** 2026-09-03
- **Decision:** Put the feed behind `adapters/market.py` (`stream()` + `history()`)
  with no provider named in the interface.
- **Why:** Alpaca is the likely first implementation, but the shock detector must
  not know which vendor produced a tick — swapping feeds should not touch
  `graph/nodes/`.
- **Outcome:** pending — no implementation.
- **Status:** Accepted

### D-07 — uv, src-layout, package `lagmatrix`
- **When:** 2026-09-03
- **Decision:** `uv` for dependency management, src-layout, import package
  `lagmatrix`, distribution name `lag-equity-matrix-ai`.
- **Why:** uv was already installed; src-layout keeps the tests honest by
  importing the built package rather than the working tree.
- **Outcome:** Working — `uv sync`, `ruff check`, and `pytest` all run clean.
- **Status:** Accepted

### D-08 — All external config through config.Settings
- **When:** 2026-09-03
- **Decision:** Nothing outside `lagmatrix/config.py` reads `os.environ`.
- **Why:** Credentials and tunables in one auditable place; also makes the whole
  pipeline configurable from a test fixture without monkeypatching the
  environment.
- **Outcome:** pending — holds trivially today because nothing is implemented.
- **Status:** Accepted

### D-09 — Parallel retrieval, joined at context_fusion
- **When:** 2026-09-03
- **Decision:** `graph_retriever` and `vector_retriever` fan out in parallel from
  `shock_detector` and rejoin at `context_fusion`.
- **Why:** The two retrievals are independent, and the latency budget is
  real-time — serializing them spends wall-clock for nothing. This is what the
  `Annotated[list[NewsChunk], add]` reducer in `LagMatrixState` exists for.
- **Outcome:** pending — wiring not written.
- **Status:** Accepted

### D-10 — Maintain this log via the spike-log skill
- **When:** 2026-09-03
- **Decision:** Keep the log through `.claude/skills/spike-log/`, not by
  convention alone.
- **Why:** A written convention decays. A skill with trigger conditions in its
  description gets reached for when a decision is actually being made.
- **Outcome:** pending — used once (this entry).
- **Status:** Accepted

### D-11 — Hooks for compaction survival and post-commit capture
- **When:** 2026-09-03T18:23:10-05:00
- **Decision:** Two hooks in `.claude/settings.json`: `PreCompact` re-injects this
  log before the conversation is summarized away; `PostToolUse` on Bash, gated
  `if: "Bash(git commit *)"`, reminds to record any decision a commit encodes.
  Declined: a `SessionStart` loader and a per-turn `Stop` check.
- **Why:** A skill only fires when the model reaches for it. These two events are
  the points where an unrecorded decision is actually lost — compaction discards
  the reasoning, and a commit is where work solidifies. A `Stop` hook would fire
  on every trivial turn.
- **Outcome:** Working, and verified surviving a restart (2026-09-03T18:27:38-05:00). The
  PostToolUse hook fires on a real `git commit ...` invocation and delivers the
  current script text. The PreCompact script produces valid output (9356 chars,
  all 8 open questions inside the cap) — but the PreCompact *event* has not been
  observed firing, since compaction cannot be forced on demand. Two things
  learned: the `if` gate matches sub-commands and bare substrings of a compound
  command, so `git add -A && git commit -m ...` triggers it and so does merely
  printing the text "git commit"; and the log outgrew an initial 8000-byte cap
  within a day, silently truncating the Open Questions section until the cap was
  raised to 24000 with an explicit truncation marker.
- **Status:** Accepted

### D-12 — Decision entries carry summary/decision/why/outcome/timestamp
- **When:** 2026-09-03T18:23:10-05:00
- **Decision:** Replace the Decision Log table with per-entry blocks holding a
  summary heading plus When / Decision / Why / Outcome / Status.
- **Why:** The table had no room for an outcome, and outcome is the field that
  closes the loop — without it the log records intent and never records whether
  the intent was right. A seven-column markdown table is unreadable; blocks are
  not.
- **Outcome:** pending — first entries written in the new format are D-11 and this one.
- **Status:** Accepted

### D-13 — ArangoDB for both halves; Qdrant dropped
- **When:** 2026-09-03T18:30:37-05:00
- **Decision:** Use ArangoDB as the single store for the market topology *and*
  the news/earnings vectors, via `langchain-arangodb`'s `ArangoVector`. Remove
  the Qdrant dependency and collapse `adapters/vector.py` onto the same driver
  as `adapters/arango.py`.
- **Why:** Qdrant over pgvector was the earlier call (D-03); the real
  alternative turned out to be *no second service at all*. ArangoDB has
  Faiss-backed vector indexes (>= 3.12.4) and an officially maintained LangChain
  `VectorStore` with hybrid RRF search. Running one engine means the graph
  traversal and the semantic lookup share a connection and a transaction — for a
  project whose thesis is fusing the two, that is the point, not a shortcut. The
  cost is a hard dependency on ArangoDB's vector maturity, which is younger than
  Qdrant's.
- **Outcome:** pending — no local instance stood up yet. Gated on Q-09.
- **Status:** Tentative — settled by Q-09

### D-14 — Differentiate on cross-asset propagation, not agent debate
- **When:** 2026-09-03T18:30:37-05:00
- **Decision:** Keep the leader→lagger network as the project's distinguishing
  feature and do not add a bull/bear debate layer.
- **Why:** Spike 01 found the multi-agent trading space saturated with forks of
  `TauricResearch/TradingAgents` (~99.8k stars) — all single-ticker debate
  frameworks with no cross-asset topology. Adding a debate layer would make this
  the 12th such repo. No GitHub project was found combining lead-lag, GraphRAG
  and ArangoDB. The gap is real; propagation across a market graph is what
  nobody has built.
- **Outcome:** pending.
- **Status:** Accepted

### D-15 — Daily cadence, multi-day horizon; minute-scale abandoned
- **When:** 2026-09-03T20:01:09-05:00
- **Decision:** The pipeline runs on end-of-day bars with a ~1-10 trading day
  prediction horizon. `Tick` becomes `Bar`, `lag_minutes` becomes `lag_days`,
  `shock_window_minutes: 15` becomes `shock_lookback_days: 60` +
  `signal_horizon_days: 10`, and `MarketFeed.stream()` is replaced by
  `daily_bars()`.
- **Why:** Minutes and days are not the same effect at two speeds. Intraday
  lead-lag is nonsynchronous-trading microstructure — mechanical, arbitraged
  fast, won on latency, and it needs no news reasoning, which would make the
  entire GraphRAG layer dead weight. The daily-to-monthly effect is slow
  information diffusion under limited investor attention (Cohen & Frazzini 2008:
  1.45%/month, t=3.61), which is exactly where reasoning over news and supply
  links does real work. Monthly is better documented, but at monthly horizons
  this degenerates into a statistical factor sort that needs no graph — so
  event-driven days is the band where this architecture earns its keep. Note
  1-10 days is an interpolation between the two literatures, not a directly
  replicated result.
- **Outcome:** pending — code retargeted and verified (ruff clean, imports OK,
  no minute-scale references remain); no data has been run through it.
- **Status:** Accepted

### D-16 — Alpaca is the sole data source; graph edges are derived, not sourced
- **When:** 2026-09-03T20:08:26-05:00
- **Decision:** All inputs come from the Alpaca API. Graph edges are built from
  two Alpaca-native sources — statistical lead-lag from daily bars
  (`adjustment=all`, SIP, ~7y, free tier) and news co-mention from the News API
  `symbols` field (Benzinga, back to 2015). The Compustat/SEC supply-chain graph
  is abandoned.
- **Why:** The alternative was a curated supply-chain graph, which is the more
  compelling story but has no Alpaca data source and no free substitute — spike
  03 called it the project's unstaffed critical path. Deriving edges costs the
  `SUPPLIES_TO` narrative and buys three things: a single vendor, free-tier
  reproducibility, and **point-in-time correctness by construction** (both edge
  types are computed from observations timestamped at or before t, so spike 03's
  look-ahead objection dissolves rather than needing engineering). It also gives
  the LLM a defensible job — labelling relationship type from co-mention article
  text — that a linear model cannot do at all.
- **Outcome:** pending — no data pulled.
- **Status:** Accepted

### D-17 — The LLM's job is graph construction, not signal generation
- **When:** 2026-09-03T20:08:26-05:00
- **Decision:** Position the LLM/GraphRAG layer as the thing that reads
  co-mention articles and assigns relationship type and direction, rather than
  as the thing that produces the trading signal.
- **Why:** Spike 03 §5 argued the analyst node competes with `beta ×
  leader_return` and probably loses. Relationship extraction from prose has no
  cheap alternative, so the LLM stops being decoration. The cost is that the
  headline demo becomes less "AI predicts the market" and more "AI builds the
  map" — a smaller claim that is actually supportable.
- **Outcome:** pending.
- **Status:** Accepted

### D-18 — LagMatrix is a corroboration layer over an exogenous signal, not a signal generator
- **When:** 2026-09-03T20:42:10-05:00
- **Decision:** The product takes an **already-generated signal** (ticker,
  direction, date) from an upstream system and returns a calibrated assessment
  of whether the graph neighbourhood corroborates or contradicts it. It does not
  originate buy/sell decisions. `shock_detector` is repurposed from "scan the
  market for shocks" to "given this name, has its leader already moved?".
- **Why:** The alternative — generating signals — required the lead-lag anomaly
  to still carry standalone tradeable alpha after 18 years of arbitrage (spike
  03 §1), and required a full factor-neutral backtest to prove anything. A
  conditioning layer needs neither: the question becomes *incremental* ("given
  the signal fired, does the graph feature move the odds?"), which is a sharper
  hypothesis, a smaller experiment, and survives even a heavily decayed anomaly.
  It also converts the evaluation target from Sharpe to **calibration**, which is
  measurable on a few hundred historical fires rather than a full backtest.
- **Outcome:** pending — blocked on the upstream signal's nature and whether a
  history of past fires with outcomes exists (Q-15, Q-16).
- **Status:** Accepted

### D-19 — The output must be able to say "contradicted"
- **When:** 2026-09-03T20:42:10-05:00
- **Decision:** The assessment is two-sided and every signal is scored, not just
  ones a human wants confirmed. Primary outputs: a calibrated odds adjustment,
  the corroborating evidence, and **the strongest disconfirming case**.
- **Why:** Spike 02 showed that a layer which can only add confidence is a
  rationalisation engine — investors are documented as seeking supporting over
  opposing evidence. Uniform application to every signal plus a pre-committed
  reading of the output is what separates a conditioning variable from
  confirmation bias. The cost is that the tool will sometimes argue against
  positions the user wants to take; that is the feature.
- **Outcome:** pending.
- **Status:** Accepted

### D-20 — Replay the upstream signal over history to manufacture the evaluation set
- **When:** 2026-09-03T20:44:22-05:00
- **Decision:** Before building the corroboration layer, codify the upstream
  signal and **replay it point-in-time over ~7 years of Alpaca bars and ~11
  years of Alpaca news** to generate several hundred to a few thousand
  historical fires. That replay set, not the existing few dozen live fires,
  becomes the evaluation dataset.
- **Why:** The alternative was evaluating on the few dozen fires we actually
  have. Arithmetic kills it: at ~25 fires per bucket the minimum detectable
  hit-rate difference is **~28pp at significance, ~40pp at 80% power**. No real
  conditional edge in equities is 40 percentage points, so that dataset cannot
  distinguish a working layer from a broken one — it can only produce a
  confident-looking number with no information in it. Detecting a plausible
  10pp effect needs ~780 fires. The signal is technical + news-driven, and
  Alpaca supplies both modalities historically, so replay is feasible; it is
  also the only path that does not require waiting months.
- **Outcome:** Never attempted, and now unnecessary. Spike 05 found 284 real BTO
  fires with derivable outcomes in oh-my-tradeagent's `audit_log` — observed
  decisions with real fills, which is strictly better evidence than a
  reconstruction. The premise that we had "a few dozen" was simply wrong.
- **Status:** Superseded by D-26

### D-21 — Orthogonality is own-name vs neighbourhood, not price vs news
- **When:** 2026-09-03T20:44:22-05:00
- **Decision:** Frame the graph layer's added information as **cross-sectional**
  — the state of *other* tickers — rather than as a different feature modality.
- **Why:** The upstream signal is both price- and news-based, which overlaps
  both proposed edge sources (statistical lead-lag; news co-mention) on
  modality. But the upstream signal is computed on the target name's own price
  and own news; the graph feature is computed on the *neighbourhood's*. Same
  modality, different data. The specific orthogonal quantity is "the leader has
  already moved and this name has not yet repriced" — which an own-name
  technical or sentiment feature cannot see by construction. This is testable,
  not assumed: overlap returns if the upstream signal contains relative-strength
  or sector-level features.
- **Outcome:** pending — measure correlation of the graph feature against the
  upstream signal score on the replay set.
- **Status:** Tentative — settled by Q-15

### D-22 — One pre-committed graph feature, chosen before looking
- **When:** 2026-09-03T20:44:22-05:00
- **Decision:** Specify a single graph feature and its decision rule in writing
  before touching outcome data; no feature search, no threshold tuning on the
  evaluation set.
- **Why:** Spike 03 §6 flagged four tunable knobs as enough to overfit any
  daily-bar dataset, and a small evaluation set makes that worse, not better.
  Pre-commitment is the only defence available at this sample size, and a
  pre-registered negative result is a genuine finding whereas a searched-for
  positive one is not.
- **Outcome:** pending.
- **Status:** Accepted

### D-23 — CandidateSource is the only seam between corroboration and scanning
- **When:** 2026-09-03T20:47:02-05:00
- **Decision:** Put the mode difference behind a single `CandidateSource`
  protocol (`adapters/candidates.py`): `ExternalSignals` now, `MarketScan`
  later. The graph takes `candidates` and returns `assessments` and is identical
  in both modes. Shock detection moves to `lagmatrix/shocks.py` as a pure
  function so the node and the future scanner share it. Mode selected by
  `Settings.candidate_source`.
- **Why:** The alternative was branching inside the graph on mode, which would
  put scan-only logic in nodes that corroboration mode has to skip. The
  observation that collapses it: sweeping the universe for shocks and traversing
  to laggers is *candidate production*, not assessment — so it belongs in the
  source. That leaves one unimplemented class as the entire cost of keeping
  scanning open, rather than a plugin framework. Node order also changed
  (`graph_retriever` now precedes `leader_state`) because a candidate's
  neighbourhood must be known before there is anything to check for shocks.
- **Outcome:** **Written, and the claim holds — narrowly.** `MarketScan` is
  implemented (13 tests), wired into the live UI as a third source, and a real
  scan at 2026-05-11 sweeps 3,204 symbols, traverses to suppliers of what moved,
  and produces 19 candidates in 8.7 s with genuine verdicts (APTV corroborated,
  CDW contradicted). **No node needed branching** — `graph/` was not touched at
  all, exactly as this entry predicted.
  **But "no node needed branching" turned out to be narrower than "no caller
  needed changes."** The scan surfaced a circularity that corroboration mode
  cannot have: the shocked leader `Y` that *originates* a candidate `X` is often
  correlated with `X`, so without intervention `Y` re-enters `X`'s own
  neighbourhood and `context_fusion` counts `Y`'s move as evidence for the
  candidate `Y` created. Selecting on a signal and then scoring on the same
  signal is the exact circularity `MarketScan` exists to remove, one hop
  removed. It is closed at the caller, by unioning `MarketScan.shocked_leaders()`
  into `signal_universe` — the mechanism D-27 already provides — and proved
  end-to-end: the test first asserts the hole is real with `signal_universe=set()`
  (a `leader_move` evidence for `Y`, verdict `corroborated`) before asserting it
  disappears with the fix. Found in plan review, not in code review.
- **Status:** Accepted

### D-24 — mattpocock/skills installed as a plugin, not vendored
- **When:** 2026-09-03T20:50:56-05:00
- **Decision:** Add `mattpocock/skills` via `extraKnownMarketplaces` +
  `enabledPlugins` in `.claude/settings.json`, rather than copying its SKILL.md
  files into `.claude/skills/`.
- **Why:** The alternative was vendoring — it would put ~100 third-party files
  into a Python repo, frozen at today's version, with no upgrade path and a
  diff that swamps the project's own history. The repo ships its own
  `.claude-plugin/marketplace.json` (its ADR 0002 says plugin distribution is
  the intended path), so one settings entry gets all 25 skills, auto-updating,
  and because it lives in committed project settings every clone gets them.
  Accepted cost: the plugin is atomic — 25 skills or none; individual ones can
  only be muted with `skillOverrides`.
- **Outcome:** pending — settings written and valid, hooks preserved, but the
  marketplace has not been fetched in this session so the skills are not yet
  confirmed loaded.
- **Status:** Accepted

### D-25 — Repo-level CLAUDE.md so the coding guidelines travel with a clone
- **When:** 2026-09-03T20:52:31-05:00
- **Decision:** Add `CLAUDE.md` at the repo root, taken verbatim from
  `multica-ai/andrej-karpathy-skills`.
- **Why:** The identical guidance already loads from `/home/ridopark/src/CLAUDE.md`
  — sections 1-4 are byte-identical — so for this machine the file is pure
  duplication. It earns its place anyway because the parent file lives *outside*
  the repository and does not survive a clone: anyone else who checks this
  project out currently gets no guidelines at all. Accepted cost: sections 1-4
  are now loaded twice per session on this machine. Note the parent file is a
  strict superset — it carries an extra "0. Design Principles" (SOLID/DRY/KISS)
  section that upstream lacks, so a cloner still gets less than the author does.
- **Outcome:** pending — file written; not yet confirmed whether the duplicate
  load is noticeable in practice.
- **Status:** Accepted

### D-26 — oh-my-tradeagent's audit_log is the evaluation set; replay abandoned
- **When:** 2026-09-03T21:12:52-05:00
- **Decision:** Use the `audit_log` table in the homelab `orchestrator` database
  (k3s ns `copytrade`, reached over SSH to 192.168.10.123) as the upstream signal
  history. 284 BTO fires, 2026-05-18 to 2026-09-03, outcomes derived by joining
  `EntryFilled` / `PartialExitFilled` / `PositionClosed` on `signal_id`. Exclude
  index-ETF candidates. Read-only access; LagMatrix never writes to it.
- **Why:** The alternative was D-20's replay, which was only ever proposed
  because we believed the fire history was a few dozen. It is 284 — and these
  are observed decisions with real fills rather than reconstructions, so no
  replay fidelity question arises. It is also the only option available: the
  signal is human options alerts posted by 10 authors to a feed, so there is no
  function to re-run even if we wanted one.
- **Outcome:** Dataset located, and the headline number corrected downward.
  Spike 07 found `audit_log` logs each alert once per tenant across 6 tenants, so
  the 284 in D-26 was rows, not fires. **98 distinct BTO signals**, 60 with
  fills. Minimum detectable effect moves from ~17pp to ~28pp. The decision to use
  this dataset stands — it is still the only fire history available and still far
  better than replay — but it supports a single pre-registered test, not tuning.
- **Status:** Accepted

### D-27 — Neighbourhoods are drawn from the wide Alpaca universe, never the signal's 26 tickers
- **When:** 2026-09-03T21:12:52-05:00
- **Decision:** When retrieving a candidate's neighbourhood, traverse the full
  Alpaca-covered universe. The 26 tickers that appear in the alert feed constrain
  which names become *candidates*, and must not constrain the neighbourhood.
- **Why:** The obvious shortcut is to build the graph over the signal universe,
  which is small and convenient. It would be fatal: that universe is NVDA, AAPL,
  AMZN, GOOGL, MSFT, META, AMD, AVGO, MU, INTC and friends — one AI-capex factor
  wearing eleven tickers. A neighbourhood drawn from it would deliver spike 02's
  confluence trap by construction, with effective independent evidence near 1.
  Traversing the wide universe is what preserves any cross-sectional information
  at all.
- **Outcome:** pending.
- **Status:** Accepted

### D-28 — ETF candidates kept, with breadth as their neighbourhood feature
- **When:** 2026-09-03T21:18:15-05:00
- **Decision:** Do not exclude QQQ/SPY candidates. Give index ETFs a separate
  feature path — breadth and dispersion across constituents — distinct from the
  single-name diffusion feature. Two feature paths, one graph, no shared
  threshold.
- **Why:** Spike 05 recommended exclusion on the grounds that an index "is the
  aggregate" and so has no neighbourhood. Half right: the diffusion feature is
  mechanically near-zero on an arbitraged ETF. But the conclusion did not follow.
  Breadth is a documented cross-sectional signal (1.19%/month long-high/short-low
  breadth, surviving size/value/skewness controls) and it is *uncomputable from
  the ETF's own price* — separating a broad 1% move from an NVDA-carried 1% move
  requires constituent traversal. On an index, naive confluence is not a bias to
  correct for, it is the phenomenon being measured, which makes ETFs arguably a
  better fit for the Q-12 machinery than single names. Excluding them would also
  have discarded 211 of 1,003 signals while power-constrained.
- **Outcome:** pending.
- **Status:** Accepted

### D-29 — Evaluate on the underlying's forward return, not option P&L
- **When:** 2026-09-03T21:22:54-05:00
- **Decision:** The outcome label for a fire is the **underlying equity's**
  forward return over the horizon, fetched from Alpaca. Option P&L becomes a
  separate downstream question, not the primary label.
- **Why:** The alternative was realised option P&L, which is what the platform
  actually trades. Three reasons it loses. It exists only for the ~60 fires that
  filled, versus 98 with underlying data — and at these sample sizes losing 38
  observations is unaffordable. Conditioning on fills biases the sample toward
  signals the execution system happened to like. And option P&L is convex,
  levered and time-decaying, so it measures strike and IV selection as much as
  directional correctness, which is not the hypothesis under test. Cost: the
  label is one step removed from the money.
- **Outcome:** pending.
- **Status:** Accepted

### D-30 — Run the directional test now; treat 98 fires as a schedule, not a wall
- **When:** 2026-09-03T21:27:37-05:00
- **Decision:** Proceed with a single pre-registered feature against the 98 fires
  available now, reporting it as a **directional read, not a validation**. Do not
  wait for the dataset to mature before building.
- **Why:** The alternative was waiting for power. Spike 08 priced it: the feed
  yields ~30 BTO fires/month, so a 15pp-detectable test is 8 months away and a
  10pp one is 23. Waiting two years to start is not a plan. Running now costs
  little, exercises the whole pipeline end to end, and the same test re-runs on a
  larger sample later at no extra design cost. The risk accepted is that a
  28pp-detectable experiment will almost certainly return "no significant
  difference" — which must be reported as *underpowered*, never as evidence of
  absence.
- **Outcome:** pending.
- **Status:** Accepted

### D-31 — Pre-registration of the first and only feature to be tested
- **When:** 2026-09-03T21:31:04-05:00
- **Decision:** Committed **before any outcome data is fetched or inspected.**
  Exactly one feature, one label, one test, no variants:
  - **Population:** the 98 distinct BTO fires, **single names only** (excludes
    SPY/QQQ) -> n≈74. ETFs remain in the product (D-28) but are out of this test.
  - **Neighbourhood:** for candidate C at date t, the 20 names with highest
    return correlation to C over the trailing 60 sessions, drawn from the wide
    Alpaca universe and excluding the 26-ticker signal set (D-27).
  - **Feature (binary):** fires when `max |neighbour return| over t-3..t-1 >= 2σ`
    **and** `|C's own return over t-3..t-1| < 0.5σ`, σ from the same trailing 60
    sessions. In words: leaders moved, C has not repriced.
  - **Label:** C's underlying forward return over the next 10 sessions (D-29),
    sign-matched to the signal's direction (BTO call = up, BTO put = down).
  - **Test:** hit rate in feature-fired vs feature-not-fired, two-proportion
    comparison. **Nothing else is computed, tuned or reported as a result.**
  - **Declared in advance:** at n≈74 the minimum detectable difference is ~33pp
    at 80% power. A null result is the expected outcome and must be reported as
    *underpowered*, never as evidence of absence.
- **Why:** The alternative is exploring features and reporting the best one,
  which on 74 observations with four tunable knobs manufactures a false positive
  with near-certainty (spike 03 §6). Pre-registration is the only defence
  available at this sample size, and it is worthless unless written down before
  the data is seen — hence logging it now, ahead of the extraction script. The
  cost is real: the first feature is a guess and probably not the best one. That
  is the price of being able to believe the answer.
- **Outcome:** **Executed, null, uninformative.** The feature fired on 2 of 64
  evaluable fires, so the minimum detectable difference was 100.6% — the test
  could not have detected anything. Base hit rate 32.8%, fired 50.0% (n=2), not
  fired 32.3% (n=62), z=+0.49. Re-run unchanged on 102 fires (D-58): identical, z=+0.49 — the new fires had no elapsed horizon. Two design errors, both made before seeing data:
  the 2σ-and-under-0.5σ conjunction was far too narrow, and the 10-session label
  was wrong for a signal whose median hold is ~1 trading day (see D-32). Spike
  09. Treat as a pilot, not as evidence.
- **Status:** Accepted

### D-32 — The signal's real horizon is ~1 trading day, not 8; Q-17 was answered on the wrong field
- **When:** 2026-09-03T21:37:35-05:00
- **Decision:** Treat the upstream signal's holding horizon as **~1-2 trading
  days**, and correct the record: Q-17 was closed using days-to-expiry when the
  relevant quantity is holding period.
- **Why:** Spike 05 read `expiry - posted_at` (median 8 days) as the horizon.
  That is when the *option* expires, not how long the position is *held*.
  `trade_context.hold_minutes` on 20 closed trades gives median **1,334 minutes
  (~22 hours)**, p90 ~2 sessions, against a mean DTE of 26.7 — the two fields
  are not close. Every outcome measured over 10 sessions therefore measured
  something the trader never held. D-15's daily cadence survives (the effect is
  still not intraday-microstructure), but its 1-10 day window should be read as
  1-2 days at the centre. Evidence is thin — n=20, three weeks — so this is
  directional, not settled.
- **Outcome:** pending — no test has yet been run at the corrected horizon.
- **Status:** Tentative — settled by Q-25

### D-33 — Second pre-registration: median split, corrected horizon, explicitly discounted
- **When:** 2026-09-03T21:41:15-05:00
- **Decision:** One further test on the same 64 observations, committed before
  re-running:
  - **Horizon:** forward return over **2 sessions**, not 10. `hold_minutes`
    median ~22h spans one overnight; p90 ~2 sessions. Two sessions covers the
    centre through p90 and is less hostage to the fact that we label
    close-to-close while real entries and exits are intraday.
  - **Feature:** the same quantity as D-31, but as a **continuous score split at
    its own median** rather than a threshold conjunction:
    `score = max|neighbour standardised 3-session move| − |candidate standardised
    3-session move|`. High score = leaders moved and the candidate did not.
  - **Split:** at the median of the score, giving 32 vs 32 by construction.
  - **Everything else unchanged** from D-31: same 20-name correlation
    neighbourhood from the wide universe, same 60-session trailing baseline,
    same point-in-time rules, same population.
  - **Declared:** even at a perfectly balanced 32/32 the minimum detectable
    difference is ~35pp. This test cannot establish a real effect. It can only
    fail to find an enormous one.
- **Why:** D-31's conjunction fired 2/64, so it had no power at any truth. The
  obvious fix — loosen the thresholds — requires choosing new numbers, and any
  number chosen after seeing a firing rate is tuned. A median split removes the
  threshold entirely: there is nothing left to pick, buckets are balanced by
  construction (which also maximises power for a fixed n), and the score
  definition follows directly from D-31's stated logic rather than from the
  results. The horizon change is independently justified by `hold_minutes`, an
  input rather than an outcome.
  **The discount, stated plainly:** this is the second test on the same 64
  observations. Its nominal significance is overstated and no p-value from it
  should be reported as if it were the first look. There will not be a third.
- **Outcome:** **Executed, inconclusive — and this is the final read on this
  dataset.** n=67 at the 2-session horizon. Re-run unchanged on 102 fires (D-58): byte-identical, still n=67. HIGH score 45.5% (n=33) vs LOW 38.2%
  (n=34); difference +7.2pp, SE 12.0pp, z=+0.60 uncorrected on a second look;
  minimum detectable 34.2pp. The sign matches the hypothesis and nothing else
  does. Both design fixes worked — the base rate rose from 32.8% (10-session) to
  41.8% (2-session), and the median split gave balanced 33/34 buckets. No third
  test; the stopping rule in this decision holds. Spike 10.
- **Status:** Accepted

### D-34 — Build the pipeline against the current data as fixtures, not evidence
- **When:** 2026-09-03T21:45:38-05:00
- **Decision:** Proceed to implement the product against the 98 fires, treating
  them as **development fixtures** rather than an evidence base. Two constraints
  follow and are binding:
  1. **No calibrated-confidence output.** `Assessment` reports evidence and a
     verdict; it must not emit a probability or an odds multiplier that implies
     validated calibration, because none exists. `odds_adjustment` stays unset
     until a powered test justifies it.
  2. **No ArangoDB yet.** The neighbourhood is a trailing correlation over
     Alpaca bars, computed in memory. Spikes 09-10 showed that needs no graph
     database at this scale.
- **Why:** The alternative was to keep analysing or to wait for power. Both were
  rejected by the user, and the reasoning is sound: spike 08 priced waiting at
  8-23 months, and spike 10 closed analysis with a stopping rule. Building now
  converts idle time into a working system while the dataset accrues at ~30
  fires/month, and the evaluation scripts already exist so re-running later is
  free. The honesty cost is real and is why constraint 1 exists — a system built
  on an unvalidated premise must not present outputs as if the premise were
  validated. Constraint 2 records that D-13/D-02 (ArangoDB) have not yet earned
  their place: it becomes right when edges get expensive to recompute or when
  co-mention/semantic edges arrive, and neither is true today.
- **Outcome:** pending.
- **Status:** Accepted

### D-35 — Adopt Send, Runtime context, checkpointing, cache and retry; decline subgraphs
- **When:** 2026-09-03T21:51:41-05:00
- **Decision:** Refactor the graph onto LangGraph v1 idioms: `Send` for
  per-candidate fan-out (replacing the runner's Python loop), `context_schema` +
  `Runtime` for dependency injection (replacing closure factories), a
  checkpointer with a `thread_id` per run, `CachePolicy` on `graph_retriever`,
  `RetryPolicy` + `timeout` on `vector_retriever`, and `interrupt()` on a
  contradicted verdict. **Explicitly decline** subgraphs, `Command` and deferred
  nodes.
- **Why:** The per-candidate loop was a workaround for cross-attribution that
  `Send` solves natively with per-branch state slices — the alternative was
  keeping a hand-rolled loop that does the framework's job worse. Closures hide
  dependencies that `Runtime` passes explicitly. No checkpointer meant a
  half-finished 98-candidate run restarted from zero and no assessment could be
  replayed, which is a real gap for a system whose product is a defensible
  judgment. The declines matter as much: subgraphs on a seven-node graph would
  be architecture theatre, and a reviewer who knows LangGraph reads gratuitous
  primitives as feature-collecting rather than design. Cost: a refactor of
  working, tested code.
- **Outcome:** Planned, and **two of the justifications above did not survive
  contact with the installed version.** `docs/plans/PLAN-2026-09-03-langgraph-idioms.md`
  (8 phases) is built on 14 executed probes against `langgraph 1.2.11`, not on
  the docs research in spike 11. (a) `error_handler` fires only when the failing
  task is alone in its superstep — under any fan-out the exception propagates and
  kills the run — so it cannot replace the news node's `try/except` as assumed;
  the plan substitutes the checkpointer's resume path, a deliberate behaviour
  change from silent degradation to halt-and-resume. (b) `CachePolicy` does not
  dedupe within a superstep, and `Send` removes the per-candidate loop that spike
  11 cited as the duplication source, so the cache yields 0 hits on a first
  batched run rather than the ~15 the loop implied; kept because D-35 decided it,
  but with a criterion scoped to what it measurably does. Also: `timeout=` is
  rejected at compile time on sync nodes, forcing `retrieve_news` to `async def`.
  Phase order was inverted from the priority list — `Runtime` (item 2) must
  precede `Send` (item 1), because a `Send` target sees only its Send argument
  and never merged state, so `closes` cannot reach a fanned-out node except
  through `Runtime.context`. Not yet implemented.
- **Status:** Accepted

### D-36 — Adopt oh-my-tradeagent's plan-then-TDD workflow, curated and adapted
- **When:** 2026-09-03T21:55:41-05:00
- **Decision:** Bring across five agents (`implementation-plan`, `quant-analyst`,
  `tdd-red`, `tdd-green`, `tdd-refactor`) and the `execute-plan` skill, adapted.
  TDD becomes a binding project rule in `CLAUDE.md`: behaviour changes go
  red → green → refactor with the failure observed before implementation, and
  the only exemptions are config, docs, `scripts/` one-offs and non-behavioural
  wiring. Plans live in `docs/plans/`.
- **Why:** The alternative was copying `.claude/` wholesale, which would have
  imported machinery this repo does not have and references that resolve to
  nothing. Two concrete breakages found: the `implementation-plan` and `tdd-*`
  agents carried **VS Code/Copilot tool names** (`edit/editFiles`, `runTests`,
  `codebase`) that are not Claude Code tools and would fail silently; and
  `execute-plan` routed to `java-architect`, GitHub issues, Discord webhooks, a
  PR-review bot and `_workspace/` paths, none of which exist here. Declined for
  the same reason: `java-architect` (this is Python), the trading-ops agents and
  skills (this repo holds no positions and reads no live state), the issue/PR
  skills (no such workflow), `testing-patterns` (Jest), and `senior-*` /
  `code-reviewer`, which would have been a third and fourth code reviewer
  alongside the built-in `/code-review` and the mattpocock plugin's.
  `quant-analyst` came over unmodified — its tool list was already correct and
  its domain is exactly this project's.
- **Outcome:** Exercised once, and it earned its place. The
  `implementation-plan` agent produced a 761-line plan that overturned two of
  D-35's justifications by probing the installed library instead of trusting the
  docs research — precisely the failure mode the plan-first step exists to catch,
  and one I would not have caught by editing directly. Separately it exposed a
  real defect in how I use agents: the run was silent for 7 minutes and the user
  had to ask. A reporting rule is now binding in both `execute-plan` and
  `CLAUDE.md`.
- **Status:** Accepted

### D-37 — Phases require executed evidence and falsifiable tests, not claims
- **When:** 2026-09-03T22:22:26-05:00
- **Decision:** `execute-plan` now requires a verification block per phase —
  criterion quoted verbatim, the exact command, its trimmed-but-unedited output,
  and MET/NOT MET — plus a stated negative control ("what would have made this
  fail?"). `tdd-red` must state, per test, the change that would break it. A
  phase without a verification block is reported as **not verified** however
  finished it looks, and evidence a sub-agent asserted without output must be
  re-run before being relayed.
- **Why:** The alternative was the existing wording, which said to validate
  against the plan's criteria but never demanded proof. PHASE-0 showed why that
  is not enough: the plan asserted "98 data rows" and nobody had executed it —
  the real number is 95, because 3 candidates produce no assessment at all. A
  confident unexecuted claim is the failure mode, and it is the same one whether
  it comes from a plan author, a sub-agent, or me. The falsifiability rule
  attacks the mirror-image failure: a test that cannot fail passes forever and
  proves nothing. Cost: more output per phase, and some of it repetitive.
- **Outcome:** Working, and it caught its author within the hour. While printing
  PHASE-0's verification block I emitted `verdict: MET` in the same output where
  `ruff check` was failing on an import-order error — the evidence contradicted
  the verdict in the block itself, which is precisely the collision the format
  exists to force. Fixed and re-verified. The negative control also earned its
  place: mutating one verdict field in the committed CSV made
  `check_baseline.py` exit 1 and name the row, proving the check can fail.
- **Status:** Accepted

### D-38 — Unverifiable is a design defect; redesign for observability rather than reporting it
- **When:** 2026-09-03T22:29:09-05:00
- **Decision:** When a criterion cannot be executed or a behaviour cannot be
  observed, do not substitute a weaker proxy and do not report it as an accepted
  limitation. Diagnose the cause — missing seam, un-injected dependency,
  nondeterministic ordering, silent early exit, or a criterion that names a
  quality rather than a behaviour — change the implementation until the
  behaviour is observable, then verify. Added to `execute-plan` with a
  symptom → defect → fix table, and to `CLAUDE.md`.
- **Why:** D-37 made a phase report "not verified" when evidence was missing,
  which normalises exactly what it set out to prevent. The alternative — treat
  unobservability as a property of the problem — is almost always wrong: it is a
  property of the code. PHASE-0 proved it immediately. The plan's "98 data rows"
  criterion looked unsatisfiable because 3 DRAM candidates vanish before the
  assessor, and the tempting fix was to relax the number to 95. The right fix
  was to give the silent path an explicit `no_assessment` outcome, which both
  satisfies the criterion as written and makes the check strictly stronger: it
  now detects *which* candidate stopped being assessable, not merely that the
  count moved. Cost: rows that are not assessments in an assessment baseline.
- **Outcome:** Applied to PHASE-0 the moment it was written. Also drove a second
  observability fix in the same phase — sorting the baseline on the full row
  rather than `(symbol, as_of, direction)`, which is not unique (98 rows, 83
  distinct tuples), so `Send`'s nondeterministic fan-out order in PHASE-2 cannot
  produce a spurious diff.
- **Status:** Accepted

### D-39 — An over-broad criterion is moved to the phase that can satisfy it, never dropped
- **When:** 2026-09-03T22:44:04-05:00
- **Decision:** PHASE-1's clause `grep -rn "def make_" src/lagmatrix/graph/`
  returns nothing is scoped to the three node modules that phase converts. The
  two factories it also matched are **not** exempted — the requirement moves to
  the criteria of PHASE-2 (`make_route_on_neighbourhood`) and PHASE-5
  (`make_retrieve_news`), the phases whose tasks remove them.
- **Why:** The clause as written was unsatisfiable against the plan's own tasks —
  TASK-1.5 instructs *keeping* `make_route_on_neighbourhood` and TASK-1.1 defers
  `retrieve_news` to PHASE-5 — so PHASE-1 could never pass it. The alternative,
  converting `vector_retriever` early, was rejected on merit: PHASE-5 also forces
  that node to `async def`, so it would be touched twice and a later baseline
  diff could not be attributed to one phase. Deleting the clause was the third
  option and the worst: the requirement is real, it just belonged elsewhere.
  Establishes the general rule — a criterion that cannot be met by the phase that
  owns it gets **relocated to the phase that can**, with the inheritance written
  into both plans so it cannot quietly evaporate.
- **Outcome:** pending — PHASE-2 and PHASE-5 now carry the inherited checks;
  neither has run yet.
- **Status:** Accepted

### D-40 — PHASE-2 keeps flat state channels alongside the keyed ones
- **When:** 2026-09-03T23:14:14-05:00
- **Decision:** `lag_edges`, `evidence` and `effective_evidence` keep their
  original flat shapes; `lag_edges_by_key`, `evidence_by_key` and
  `effective_evidence_by_key` are added alongside and are what the nodes
  actually read. `leader_shocks` and `news` went fully dict-keyed as the plan
  specified. Deviates from PHASE-2's literal state table.
- **Why:** The plan's state table and its own criterion 2 are mutually
  incompatible. Converting `lag_edges` to a dict makes the protected assertion
  `{e.leader for e in out["lag_edges"]}` iterate string keys and raise
  `AttributeError`, and makes `effective_evidence <= len(evidence)` a
  `dict <= int` `TypeError` — so satisfying the table would have broken assert
  lines that criterion 2 forbids touching. The alternative, editing those
  assertions, is an explicit halt condition. Carrying both shapes is a genuine
  DRY cost and the flat channels are now compatibility surface rather than
  production path.
  **Verified the deviation does not hollow out the safety net**, which was the
  real risk: injecting a D-27 violation into `graph_retriever`'s producer made
  both protected tests fail, and restoring it made them pass — so the flat
  channel is a faithful projection of the same computation, not a parallel that
  can drift. Green also found the deeper reason keying is needed beyond the
  fixtures: 83 distinct `(symbol, as_of)` pairs across only 24 symbols means
  filtering by `e.lagger == c.symbol` alone cannot disambiguate the same symbol
  on different dates once batched.
- **Outcome:** pending — works and is tested; the flat channels remain a
  cleanup candidate for whenever criterion 2's protection is lifted deliberately.
  PHASE-3 references `values["lag_edges"]` by name, so the flat shape is load-
  bearing there too.
- **Status:** Accepted

### D-41 — Phase criteria must exercise every branch the phase creates
- **When:** 2026-09-04T05:46:44-05:00
- **Decision:** A phase that adds or changes a code path must run that path in
  its completion criterion. PHASE-2's criterion is reopened to include
  `run_pipeline.py --limit 2 --news`, and `LagMatrixContext.news_client` becomes
  an injected dependency so the news branch is testable without the network.
- **Why:** PHASE-2 passed every clause of its criterion — 15 tests, ruff, all 98
  baseline rows identical — while having shipped a hard crash. It changed the
  `news` channel to a dict reducer and left `vector_retriever` returning a list,
  and nothing noticed because every test and both smoke checks ran
  `with_news=False`. The reported evidence was accurate and the conclusion was
  wrong, which is worse than a missing receipt: the receipts were all real.
  This is the inverse of D-39's problem — those criteria were too broad and
  failed loudly on literal reading; this one was too narrow and passed quietly
  on a broken build. **Over-broad criteria cost a halt; over-narrow criteria
  ship bugs.** The root cause is the one D-38 names: `make_retrieve_news`
  constructs its own `NewsClient` from `os.environ`, so the branch could not be
  tested without the network and therefore never was — the un-injected
  dependency is why it broke *and* why nothing caught it.
- **Outcome:** Fixed and verified. `retrieve_news` is now a module-level
  function returning candidate-keyed news, and reads `runtime.context.news_client`
  when set. Proved the injection is real rather than trusting it: with
  `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` unset and no monkeypatch, a fake wired
  only through `context.news_client` was called once and produced news keyed
  `CAND|2026-06-01` — had the injection not been built, the env fallback would
  have raised `KeyError`. Worth recording that the test could not have forced
  this: it wired the fake through both the context and a monkeypatch, so a
  shape-only fix would have passed it. A test pins only what it observes.
  PHASE-2 now MET on the amended criterion: 17 tests, ruff clean, 98 baseline
  rows identical, `--news` exits 0.
- **Status:** Accepted

### D-42 — `unlazy` vendored as a skill; its Stop hook deliberately not installed
- **When:** 2026-09-04T11:20:47-05:00
- **Decision:** Vendor `Leonxlnx/unlazy` (MIT, 3k stars) into
  `.claude/skills/unlazy/` — SKILL.md, SECURITY.md, `references/`, `templates/`,
  `scripts/`. Leave `tests/`, `research/`, `.github/` and the changelog behind.
  **Do not run `scripts/install-hooks.mjs`**; no Stop hook is registered.
- **Why:** Unlike `mattpocock/skills` (D-24) this repo ships no
  `.claude-plugin/marketplace.json`, so vendoring is the correct install rather
  than the lazy one — there is no plugin path to prefer. Only the files SKILL.md
  actually references were copied; the ~200 KB test suite is the upstream
  project's own and adds nothing to using the skill. The Stop hook is withheld
  on purpose: it rewrites `.claude/settings*.json`, this repo already runs two
  hooks that matter (PreCompact decision-log injection, post-commit spike-log
  prompt), and a completion-gating hook changes when a session is allowed to
  finish. That is the user's call, not a side effect of installing a skill —
  and the skill's own text warns against letting content talk an agent into
  installing a hook.
- **Outcome:** Skill verified working (`gate-lint.mjs` warns correctly on the
  manual gate; `gate-check.mjs --status` reports unmet gates without executing
  anything). The Stop hook was subsequently installed **local-only** at the
  user's explicit request — `.claude/settings.local.json`, gitignored along with
  `.unlazy/` and `.unlazy-hook-state.json`; the committed
  `.claude/settings.json` and its PreCompact/PostToolUse hooks are untouched.
  Proved both directions rather than assuming: with no ledger present the hook
  exits 0 silently (inert), and with a `GATES.md` holding one unmet gate it
  emitted `{"decision":"block", ...}` naming `GATES:G1`. Cleaned up afterwards.
  Reading the source also confirmed two claims that mattered: `stop-hook.mjs`
  contains no `child_process`/`spawn`/`execFile` — it never runs `CHECK:` lines,
  only compares recorded evidence structurally — and `MAX_BLOCKS = 6` releases
  the session after six blocks without gate progress, so a malformed gate cannot
  trap it.
- **Status:** Accepted

### D-43 — Leveraged and inverse products excluded from correlation neighbourhoods
- **When:** 2026-09-04T12:41:19-05:00
- **Decision:** Filter leveraged and inverse products out of a candidate's
  neighbourhood. Classified from Alpaca asset names by
  `scripts/build_exclusions.py` (185 of 3,211 symbols) into
  `data/excluded-etfs.csv`, injected as `LagMatrixContext.excluded_symbols` and
  applied in `graph_retriever` alongside — not merged with — the D-27
  signal-universe exclusion. The runner fails soft to an empty set when the file
  is absent. Baseline deliberately re-captured.
- **Why:** Building the graph from return correlation cannot distinguish a
  supplier from a *derivative of the candidate itself*. Real run, AAPL
  2026-08-04: `AAPD moved +3.07σ while AAPL moved -3.00σ` — AAPD is a -1x AAPL
  ETF and AAPU a 2x, so both correlate near-perfectly with AAPL because they
  *are* AAPL. They inflated the evidence count while carrying zero independent
  information, and bloc weighting only halved them. The alternative — excluding
  all ETFs — was rejected because D-28 keeps SPY and QQQ as candidates with
  breadth as their feature. Classification is a **conjunction**: the name must
  read as a fund AND carry a leverage token, which is what keeps Build-A-Bear,
  10x Genomics, Ultragenyx, Ultra Clean and Ultrapar out of the list.
- **Outcome:** **15 of 98 rows changed, 3 verdicts flipped** (GOOGL 2026-08-10
  and HOOD 2026-06-29 neutral→contradicted, MSFT 2026-07-01 neutral→corroborated);
  distribution moved 77/10/8 → 74/12/9. The mechanism was not what I predicted:
  because `topk` is fixed at 20, dropping a leveraged ETF does not shrink the
  neighbourhood, it **promotes the next real name into it** — so several
  candidates gained neighbours rather than losing them (HOOD's contradicting
  count went 1→3). The exclusion substitutes signal for reflection rather than
  merely removing noise. Also caught during development: the first classifier
  missed FNGD/FNGU (`-3X Inverse Leveraged ETNs`) because `\betn\b` does not
  match the plural — found by printing near-misses rather than inspecting hits.
- **Status:** Accepted

### D-44 — Checkpoint serialization registered via `allowed_msgpack_modules`, not `with_allowlist`
- **When:** 2026-09-04T13:05:13-05:00
- **Decision:** Construct the checkpointer's serializer explicitly —
  `JsonPlusSerializer(allowed_msgpack_modules=[("lagmatrix.domain.models", name) ...])`
  naming every domain model. Do **not** use `saver.with_allowlist()` and do
  **not** set `LANGGRAPH_STRICT_MSGPACK`. Tests assert the absence of the message
  with `caplog`, after clearing the process-global dedup set.
- **Why:** The plan's V14 was wrong in two independent ways, both found by
  probing rather than reading. `with_allowlist()` is a **no-op** at the installed
  default — `allowed_msgpack_modules` defaults to the sentinel `True` and the
  method short-circuits `return self`; `clone is saver` proves it. And the
  message is emitted via stdlib `logging`, not `warnings.warn`, so the plan's
  suggested `pytest.warns`/`recwarn`/`filterwarnings("error")` assertion would
  have been a **silent no-op** — a test that passes identically whether or not
  the fix exists. That is the same class of defect as D-41, caught before
  shipping this time rather than after.
  Strict mode was the other candidate and was rejected: it *blocks* anything
  unlisted, trading a deprecation warning for a runtime crash whenever a model is
  added and someone forgets the list. `allowed_msgpack_modules` alone suppresses
  the message, still round-trips correctly, and is the path the library's own
  warning text recommends.
  Not doing nothing: the message reads *"This will be blocked in a future
  version"* — it is a deprecation, so ignoring it defers a break to an upgrade.
- **Outcome:** Verified by probe before adoption: default emits the warning,
  `allowed_msgpack_modules=[7 models]` emits none, and both round-trip a
  `Candidate` back as a `Candidate`. Not yet implemented in `runner.py`.
- **Status:** Accepted

### D-45 — SqliteSaver checkpointer, thread_id per run, replay as a script
- **When:** 2026-09-04T13:15:58-05:00
- **Decision:** `build_graph(*, with_news=True, checkpointer=None)`; the runner
  opens `sqlite3.connect("data/checkpoints.sqlite")` itself and constructs
  `SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[...]))`
  over all seven domain models. `run()` returns `(assessments, thread_id)` —
  the id is always handed back. `scripts/replay.py <thread_id>` walks
  `get_state_history`. **Answers Q-08.**
- **Why:** `InMemorySaver` was the cheap alternative and fails both motivations:
  it dies with the process, so a 98-candidate run that crashes halfway is
  unrecoverable in exactly the scenario that justifies checkpointing, and there
  is nothing left to replay tomorrow — shipping it would have made D-35 item 3
  decorative. Postgres has not earned a server process at this scale.
  `from_conn_string` had to be abandoned mid-implementation: its signature is
  `(conn_string: str)` with no `serde` parameter, while `__init__` is
  `(conn, *, serde=None)` — so opening the connection by hand is the only route
  to an allowlisted serializer, verified by reading both signatures.
- **Outcome:** Verified end to end on a cold process, not just in tests. A fresh
  run printed `thread_id: all-988a1d8e`; `replay.py` on that id reproduced the
  topology from disk — **step 0 shows six concurrent `graph_retriever` tasks and
  step 1 six `leader_state`, collapsing to a single `context_fusion` at step 2**,
  which is the `Send` fan-out and fan-in visible in the checkpoint history. 7
  checkpoints, 360 KB, readable from a separate process. 22 tests pass, all 98
  baseline rows identical (durability must not change answers), no
  unregistered-type warning in any run, and neither `with_allowlist` nor
  `LANGGRAPH_STRICT_MSGPACK` appears anywhere in `src/` or `scripts/`.
- **Status:** Accepted

### D-46 — CachePolicy kept, and measured at zero hits
- **When:** 2026-09-04T14:29:29-05:00
- **Decision:** Ship `CachePolicy(ttl=3600)` on `graph_retriever` with the
  default `key_func`, `cache` threaded through `build_graph`, a fresh
  `InMemoryCache()` per `run()` call — **and record that it buys nothing in
  current usage.** Two tests pin the behaviour, one of them deliberately pinning
  a limitation rather than a feature.
- **Why:** Spike 11 justified this item with *"with the per-candidate loop,
  repeats it for every candidate on the same date"*. PHASE-2 deleted that loop,
  so the justification is gone. Keeping it anyway rests on a narrower claim that
  is true and tested — caching works **across invocations** of one compiled
  graph sharing a cache object — and on the deferred `MarketScan` mode (D-23)
  being exactly the long-lived process that would exploit it. The alternative,
  removing it, was seriously considered: an inert feature retained because an
  earlier decision said so is the feature-collecting D-35 declined subgraphs to
  avoid. It survives on the condition that the measurement travels with it.
- **Outcome:** **98 candidates, 98 node executions, 0 cache hits** on the real
  data — reproduced independently, not taken from the agent. Exactly zero, not
  approximately: V12 means no dedup within a superstep, so the 13 duplicate
  `(symbol, as_of)` pairs each execute, and a fresh cache per `run()` leaves no
  cross-invocation reuse either. `tests/test_caching.py::test_cache_does_not_
  dedupe_within_one_invocation` exists to stop a future reader inferring
  deduplication from the mere presence of a cache; if it ever fails, the library
  changed, not this repo. Hazard recorded in `builder.py`'s docstring: the
  default `key_func` pickles the `Candidate` but **not** `closes`, so a graph
  reused across two bar frames would serve a stale neighbourhood — mitigated by
  `runner.py` scoping the cache to the frame, with `ttl` as belt-and-braces.
- **Status:** Accepted

### D-47 — News failures halt and resume instead of degrading silently
- **When:** 2026-09-05T10:40:37-05:00
- **Decision:** `retrieve_news` becomes `async def` with
  `RetryPolicy(max_attempts=3)` and `timeout=30.0`; its `try/except` is
  **deleted** and failures propagate. `runner.run` becomes async with a
  `run_sync` wrapper. The checkpointer swaps to `AsyncSqliteSaver`.
- **Why:** Today an Alpaca outage is swallowed and the run reports assessments
  built on missing news — for a product whose output is a defensible judgment
  (D-19, D-34), that silent degradation is the bug. After PHASE-3 a propagated
  failure is a resumable pause, not a lost run, so halting became affordable.
  `error_handler` was the obvious framework substitute and does not work:
  re-probed, it **does** fire under a `Send` fan-out but stops *suppressing* the
  exception once two or more tasks share a superstep — with one task it does
  both. The plan's V9 wording ("fires only when alone") reached the right
  conclusion by the wrong route.
- **Outcome:** 27 tests, ruff clean, 98 baseline rows identical, `--news` exits 0
  against live Alpaca. Structural wiring confirmed rather than assumed:
  `nodes["vector_retriever"].retry_policy` is a `RetryPolicy(max_attempts=3)`
  and `.timeout` a `TimeoutPolicy(run_timeout=30.0)` — a clause I added mid-phase
  because red's timeout test necessarily uses its own one-node graph and asserts
  nothing about production. **Two cascades the plan did not anticipate.**
  (1) V7 understates the blast radius: one `async def` node breaks sync
  `.invoke()` for the *entire graph* — `TypeError: No synchronous function
  provided` — with no timeout involved, so every caller must move to `ainvoke`.
  `tests/test_news.py` was adapted for that (helper and tests to async;
  assertion md5 `bf857ff981d21994f4f1de99c16a2f82` identical before and after,
  5 assertions untouched) — by me, not by green, since the rule against green
  editing tests exists to stop a green phase weakening assertions to pass.
  (2) `SqliteSaver` raises `NotImplementedError` on every async call, so
  PHASE-5's conversion broke PHASE-3's checkpointer; `AsyncSqliteSaver` replaces
  it, using `aiosqlite` which was already an installed transitive dependency.
  Verified cross-writer: `scripts/replay.py`'s **sync** saver still reads a
  database written by the **async** one, topology intact. Async stayed confined
  to `vector_retriever.py` and `runner.py` — no second node converted.
  **Third cascade, found afterwards:** `scripts/capture_trace.py` drives the
  graph with sync `stream()` and broke silently — it is outside both the test
  suite and the completion criteria, the same blind spot that shipped PHASE-2's
  `--news` crash. Converted to `astream`. Containment was verified for *nodes*;
  the gap was *callers not named in the criteria*.
- **Status:** Accepted

### D-48 — `interrupt()` on contradicted verdicts, in a separate pure `review` node
- **When:** 2026-09-05T17:42:35-05:00
- **Decision:** A new `review` node between `assessor` and `publisher` calls
  `interrupt()` when any verdict is `contradicted` **and**
  `LagMatrixContext.halt_on_contradicted` is set. Default is `False`, so the
  ordinary path never pauses. `scripts/run_pipeline.py --review` opts in, prints
  the contradicted candidates with their rationales, and returns the `thread_id`
  needed to resume. Completes D-35 item 6 and the refactor.
- **Why:** `interrupt()` raises, so an interrupting node produces **no writes** —
  probed and confirmed: after the first invoke the prior node's write survived
  and the interrupting node's key was absent from state. Calling it inside
  `assess` would therefore discard the very assessments the human needs to read.
  A separate node also has to be **pure**, because LangGraph re-executes an
  interrupted node from the top on resume; `assess` cannot be that node since it
  stamps `datetime.now(UTC)`, which would silently rewrite every timestamp on
  resume. `review` holds no clock, randomness or uuid — verified.
  `Command` appears only as the resume input to `ainvoke`; no node returns one,
  so D-35's decline of it as a routing primitive is intact and the builder
  docstring says so.
- **Outcome:** 31 tests, ruff clean, 98 baseline rows identical, both protected
  assert-line md5s unchanged. The safety property was verified on production
  data rather than a fixture: NFLX 2026-06-02 resolves `contradicted` and runs
  **straight through** on the default path — a wedged `check_baseline.py` was
  the failure mode red's TASK-6.4 existed to catch. `--review` over 20 real
  candidates surfaced four for confirmation; SPY 2026-06-16 shows 20 raw
  neighbour moves weighted to **1.00 effective** (0.15 for / 0.85 against),
  which is the independence discount arriving exactly where a human is asked to
  decide. Green declined to build a CLI resume path, correctly — TASK-6.7 asked
  only for the thread id, and `tests/test_review.py` already demonstrates
  `ainvoke(Command(resume=True), config)`.
- **Status:** Accepted

---

### D-49 — Commit the baseline's expected side; leave its inputs out
- **When:** 2026-09-05T20:21:42-05:00
- **Decision:** `data/baseline-98.csv` and `data/excluded-etfs.csv` are tracked
  via a `data/*` + negation pair; every other file in `data/` stays ignored,
  including `data/bars.parquet` and `data/fires.csv`. The repo therefore carries
  the 98 expected verdicts and the exclusion list that shaped them, but not the
  bars or the alert feed they were computed from.
- **Why:** Committing `bars.parquet` too — the only option that would make
  `check_baseline.py` green on a fresh clone — lost on two counts: it is 17 MB of
  Alpaca vendor data we have no right to redistribute, and it is a binary blob
  that would grow the history on every re-pull. Dropping the golden file instead
  lost because it is the artefact that caught the PHASE-2 `--news` regression
  (D-41); an unreproducible guard still beats no guard. `excluded-etfs.csv` was
  added on the same reasoning one step later: D-43's list is a *point-in-time*
  snapshot of Alpaca's asset metadata, so re-running `build_exclusions.py`
  returns a different set as products launch and delist — it is an input to the
  baseline (it flipped three verdicts), not a derivation of it, and shipping it
  is the only way those three flips stay explicable. The residual cost is that
  the guard is now half-portable, which is what Q-27 is for. Note the mechanism:
  `data/` had to become `data/*`, because git never descends into an excluded
  *directory* and the negation would have been silently unread.
- **Outcome:** Half-right. The tracked files were the correct choice, but the
  gap they left was wider than recorded: `run()` reached `data/fires.csv`
  through a *second*, uninjected call site that Q-27 did not name. Closed by
  D-50 — verified by materialising a tracked-files-only tree and reproducing
  the 98 rows there, twice (before the fix it failed, after it passed).
- **Status:** Superseded by D-51 — the data it tracked is no longer published

### D-50 — Committed input projections plus an injected signal source, so the guard runs from a clone
- **When:** 2026-09-06T01:40:00-05:00
- **Decision:** `scripts/build_fixture.py` freezes two projections of the
  uncommitted inputs — `tests/fixtures/closes.parquet` (close column only, all
  3,210 symbols, all 158 sessions, float64) and `tests/fixtures/fires.csv`
  (ticker/posted_at/direction only). `check_baseline.py --fixture` reads them.
  `run()` gains a keyword-only `signals` parameter so the candidate source can be
  injected rather than constructed from a hard-coded path.
- **Why:** Projections, not samples. A slice down to the 98 candidates' own
  symbols — which is what Q-27 originally proposed — lost on inspection:
  `graph_retriever` ranks each candidate against the *whole* universe to choose
  neighbours, so dropping symbols would silently change every neighbourhood and
  the fixture would reproduce a different number than the one it claims to
  guard. float32 lost too: it saves 8% (4.01 → 3.67 MB) at 2.3e-4 error on a
  close, enough to reorder a correlation rank and flip a verdict, which defeats
  a byte-exact guard. Trimming the 30 leading sessions lost for the same class
  of reason — it changes how many rows the trailing windows see. The `signals`
  injection beat threading a path argument down because `run()` read the default
  twice (candidates *and* `signal_universe`), and a path argument invites fixing
  one and missing the other, which is exactly what happened first.
  `--fixture` is opt-in rather than an automatic fallback: a silent fallback lets
  the guard report "unchanged" while the real inputs are missing.
- **Outcome:** Working. Reproduces all 98 rows from a tree containing only
  `git ls-files` output plus the fixtures — 40s, exit 0. Negative control: 5%
  iid noise on every cell produces 80 field diffs and exit 1, so the guard is
  reading the fixture, not short-circuiting. A single perturbed symbol (CBC,
  +15% over 40 sessions) changed nothing — a real property, not a defect: one
  symbol in 3,210 need not enter any top-20. The portability it bought was then
  given up by D-51; the `signals` injection it introduced was kept, and is the
  part that had standalone value.
- **Status:** Superseded in part by D-51 — the fixtures are no longer published;
  the injection seam stands

### D-51 — Publish the code, not the data; the baseline guard stays local
- **When:** 2026-09-06T02:45:00-05:00
- **Decision:** `data/baseline-98.csv`, `tests/fixtures/closes.parquet` and
  `tests/fixtures/fires.csv` are removed from the repository and from its
  history, and re-ignored. `data/excluded-etfs.csv` stays tracked. The
  `signals` injection (D-50), `scripts/build_fixture.py` and
  `check_baseline.py --fixture` all stay — the tooling works, it just runs
  against locally held inputs. The guard is therefore not runnable from a clone,
  and the README says so plainly instead of implying otherwise.
- **Why:** Reverses the publication half of D-49 and D-50 on the owner's
  instruction after the repo had been public for roughly four minutes (zero
  forks, zero stars, then set private). What the fixtures bought — a reviewer
  reproducing the 98 verdicts unaided — lost against what they disclosed: 3.9 MB
  of Alpaca-derived closes, and the tickers, dates and directions the real signal
  feed fired on. `excluded-etfs.csv` survives the cut because it carries product
  *names* only, no prices and no signal content, and `runner.py` loads it at
  startup, so keeping it is the difference between the pipeline running on a
  clone and not. Deleting and recreating the GitHub repository beat a force-push:
  force-pushed blobs stay reachable by SHA until GitHub garbage-collects, on no
  schedule we control. Two commit messages were reworded in the rewrite because
  they described committing files that no longer exist — a rewrite that leaves
  the log lying about its own contents is worse than no rewrite.
- **Outcome:** Done. Old remote deleted at 0 forks / 0 stars, recreated public,
  rewritten history pushed. Verified by cloning the published repo back: 108
  files, 6 commits, `data/` holds only `excluded-etfs.csv`, `tests/fixtures/` is
  absent, no withdrawn path appears in any tree, largest blob is `uv.lock` at
  411 KB, and the suite passes from the clone (33). Residual risk unquantifiable
  by construction — the repo was public ~4 minutes, and GitHub-side caches and
  third-party scrapers are outside what we can inspect.
- **Status:** Accepted

### D-52 — A synthetic universe restores the guard that D-51 made unrunnable
- **When:** 2026-09-05T22:50:39-05:00
- **Decision:** `scripts/make_synthetic.py` fabricates a 166-symbol, 100-session
  universe with six candidates, each constructed to land on a chosen verdict
  branch. `check_baseline.py --synthetic` diffs against its golden file, and
  `tests/test_synthetic_baseline.py` runs the same comparison inside the suite.
  `--fixture` and `build_fixture.py` are removed; the real projections move to
  `data/fixtures/` where the existing ignore rule covers them.
- **Why:** D-51 withdrew the real inputs, which left the guard runnable only on
  one machine. Publishing anonymised or shuffled real bars lost — the values
  would still be Alpaca's, so it buys nothing on the disclosure question that
  motivated D-51. Generating from a seed at run time, with no committed prices,
  lost to committing a 237 KB fabricated parquet: a seeded generator makes the
  golden file hostage to the RNG and to library versions, whereas a frozen file
  moves the only remaining variance into floating-point reduction order.
  Keeping three modes (real / real-projection / synthetic) lost to two: the
  projection mode's sole benefit was skipping a pivot.
  The honest cost: this guards pipeline behaviour, not market truth. It cannot
  catch a change that alters real verdicts while leaving synthetic ones intact.
  The offsetting gain is that the real data only ever exercised the branches the
  market happened to produce — it never once produced a `w_pro == w_con` tie,
  which the synthetic set now covers — and 1s in the suite is enforced where 40s
  in a script is remembered.
- **Outcome:** Working. Six rows, all four verdict values present, reproduced
  from a tracked-files-only tree in 1.0s. Negative control: flipping one bloc's
  shock moves SYNA from 16/0 to 6/10 and exits 1. Byte-identical across
  OPENBLAS/OMP/MKL thread counts 1, 2 and 8, so reduction order does not move
  it on this machine. Cross-architecture stability, recorded here as the
  residual risk, was then closed by D-53: byte-identical on a real aarch64
  runner. Real-data guard unchanged (98 rows identical). Suite 35.
- **Status:** Accepted

### D-53 — Two-architecture CI, because the golden file encodes floating-point order
- **When:** 2026-09-06T04:02:00-05:00
- **Decision:** `.github/workflows/ci.yml` runs ruff, the suite, the synthetic
  guard and a byte-for-byte regeneration of the golden file on both
  `ubuntu-latest` (x86_64) and `ubuntu-24.04-arm` (aarch64). Each job asserts
  `uname -m` before doing anything else.
- **Why:** D-52 froze verdicts that depend on floating-point reduction order in
  the correlation and rolling-std maths, and left cross-architecture stability
  untested. Local emulation lost because it was not available — no docker daemon
  in this WSL distro, no qemu binfmt handler — and because emulated FP would not
  have answered the question anyway: QEMU does not reproduce a native BLAS
  kernel's reduction order, so a green emulated run would have proved nothing.
  GitHub's arm64 runners are free for public repos, run on real hardware, and
  re-check on every push instead of once. The `uname -m` assertion exists
  because a matrix entry that silently fell back to x86 would report a green
  aarch64 job — the failure mode that would make this whole exercise worthless.
- **Outcome:** Passed first run, both architectures. On aarch64: ruff clean,
  35 passed, guard `6 rows identical`, and the regenerated golden file diffed
  `identical on aarch64`. So the topk boundary visible in SYNA (16 of 20
  neighbours selected, not a clean sweep) does not resolve differently under a
  different BLAS kernel and SIMD width. Closes D-52's residual risk.
- **Status:** Accepted

### D-54 — Drop the candidate's own column from its correlation pool
- **When:** 2026-09-05T23:46:40-05:00
- **Decision:** `graph_retriever` unions `{c.symbol}` into `drop_cols`, so a
  candidate is excluded from its own correlation pool unconditionally rather
  than only when it happens to appear in `signal_universe`. Closes Q-26.
- **Why:** The alternative was leaving it, on the argument that D-27 already
  excludes every alert ticker and production is therefore correct. That lost
  because the correctness was accidental — it depended on candidates being drawn
  from the signal feed, which `MarketScan` (D-23) would stop being true, and
  nothing in the code said so. Fixing it now costs one line and cannot regress
  production: both baselines are byte-identical after the change (98 real rows,
  6 synthetic), which is the proof rather than the hope.
- **Outcome:** Working, and it cost more than one line to land honestly. Both
  guards byte-identical (98 real, 6 synthetic), so production is provably
  unaffected. Two things the question had not recorded: the self-edge also
  *displaced* a real neighbour from the top-k, and two existing tests were
  passing **because of** it — see D-55.
- **Status:** Accepted

### D-55 — Rebuild the two fixtures that were green only because of the Q-26 bug
- **When:** 2026-09-05T23:52:00-05:00
- **Decision:** `test_fuse_evidence_effective_evidence_never_exceeds_raw_count`
  and `test_two_candidates_do_not_cross_attribute` get bespoke local universes
  containing a genuine ≥2σ shock in the leader blocs, and each gains a direct
  assertion that the candidate's own symbol is absent from its evidence.
  `tests/conftest.py`'s shared `closes` fixture is left untouched.
- **Why:** Applying D-54 turned both tests red. The cause was not the fix: in the
  shared fixture at those tests' `as_of`, **no** real leader crosses σ=2.0
  (LEAD1 −0.64, LEAD2 −0.86, LEAD3 −0.34, INDEP −0.00). The only shock was the
  candidate's own −1.02, which `leader_state` emits unconditionally for
  `sym == c.symbol`. So the self-edge was the sole evidence unit those fixtures
  ever produced, and both tests were asserting against the bug's artefact.
  `test_two_candidates_do_not_cross_attribute` is the guard for PHASE-2's
  cross-attribution fix, so the regression test protecting one bug was itself
  standing on another. Editing the shared fixture lost to bespoke local ones:
  `closes` backs `test_review`'s empirically-pinned verdicts, and injecting a
  shock anywhere in its 120 sessions moves some other test's rolling baseline.
  Two findings worth keeping. First, overlapping shock windows leak: a
  simultaneous move in both blocs dominates Pearson correlation over the 60-day
  window enough that one bloc's leader enters the *other* candidate's top-k —
  reproduced across ~15 seeds. Temporally disjoint windows with the inactive
  bloc held flat make cross-bloc correlation NaN by construction rather than
  small by luck. Second, `effective_evidence ≤ raw count` is close to
  unfalsifiable: weight is `1/bloc` and never exceeds 1, so the inequality holds
  whatever the code does. It survived the revert test and needed the explicit
  self-symbol assertion added before it could fail at all.
- **Outcome:** 38 passed, ruff clean, both baselines unchanged. Verified by
  reverting the D-54 fix: all four tests fail without it and pass with it, so
  they are falsifiable rather than merely green.
- **Status:** Accepted

### D-56 — Widen by targeting prolific alerters, not by adding channels
- **When:** 2026-09-06T00:01:08-05:00
- **Decision:** Treat Q-24's answer as: the mechanism is trivial (one sidecar per
  channel, one env var, no author allowlist), but the *targeting* is the whole
  problem. Prioritise finding channels containing a high-volume alerter over
  adding channels generally. Spike 13 has the numbers.
- **Why:** Spike 08 concluded "adding sources scales the dataset linearly" and
  that is wrong — it assumed ten comparable authors without checking. One author,
  TradingTheTrend, is 78 of 102 BTO fires (76%) and 186 of 238 STCs (78%); the
  median contributing author produces ~2 fires per quarter, and three of the ten
  "authors" are the system and its owner. So a new channel is a draw from a
  distribution whose mass sits almost entirely in a rare prolific account. One
  more TradingTheTrend-class source cuts the time to a 15pp-detectable test from
  8.3 months to 4.5; ten median authors only get it to 5.3. The alternative
  reading — keep waiting, since 30/month is steady — loses on both counts: it is
  slower, and it accumulates more of the same author (Q-28).
- **Outcome:** pending — no channel has been added yet; this records where to aim.
- **Status:** Accepted

### D-57 — The baseline tracks the live feed, and its filename stops counting
- **When:** 2026-09-06T05:46:21-05:00
- **Decision:** Refresh `data/fires.csv` and `data/bars.parquet` from source, and
  re-baseline at the new candidate count. `data/baseline-98.csv` becomes
  `data/baseline.csv`; scripts and README follow.
- **Why:** The count in the filename was a lie waiting to happen — the feed adds
  ~30/month, so any name encoding a size rots on the next refresh. Freezing the
  extract at 98 instead, to keep the guard's inputs constant, lost because the
  guard's contract is "same inputs, same outputs", not "inputs never change":
  pinning it would have meant the pipeline was never again exercised on the data
  it exists to process.
  Two things this refresh established that a straight re-run would have hidden.
  **Zero drift:** refreshing the bars moved the liquidity-filtered universe from
  3,210 to 3,205 symbols, and *not one field* of the original 98 rows changed —
  so neighbourhood selection is not sensitive to small universe churn, which was
  an open worry rather than a measured fact. And the four new fires
  (2×INTC, 2×QQQ, all 2026-09-04, all `up`) came back `neutral` with 0.0
  evidence, not `no_assessment` as predicted: `sessions > str(as_of)` compares a
  tz-aware index against a date string, so the candidate's *own* session is
  selected as `ti` and the window is `[ti-60, ti)`, ending strictly before the
  alert date. Point-in-time holds; the prediction was wrong, not the code.
- **Outcome:** Working. 102 rows, both guards green, suite 38, ruff clean.
- **Status:** Accepted

### D-58 — Power is counted in *evaluable* rows, not collected fires
- **When:** 2026-09-06T05:49:03-05:00
- **Decision:** Re-ran both pre-registrations unchanged on the refreshed 102-fire
  extract. Both results are identical to the originals — D-31 z=+0.49, D-33
  HIGH 45.5% (n=33) vs LOW 38.2% (n=34). Record the schedule in evaluable rows
  from now on: spike 08's projection counted collected fires and overstates the
  pace by ~1.5x.
- **Why:** Not one of the four new fires entered either test. They are dated
  2026-09-04 and the bars end 2026-09-04, so there is no forward return to score
  yet — the outcome window has not elapsed. That is the general case, not a
  quirk: **the testable dataset always lags the collected one by the evaluation
  horizon.** Measuring the whole funnel on this run: 102 collected → 76 single
  names (26 ETFs excluded by D-31's design) → 67 evaluable (9 lost to missing
  bars, short history, or an unelapsed horizon). End-to-end yield 65.7%.
  So a 15pp-detectable test needs ~530 collected fires to produce 348 evaluable
  rows, not 348 collected. At today's 30/month that is 14.3 months, not the 8.2
  spike 08 projected; with one more prolific source (D-56), 7.8 rather than 4.5.
  The alternative — leaving the projection in collected fires because it is the
  number the feed produces — lost because it is the number that decides when to
  run a conclusive test, and being 1.5x optimistic about that is exactly the
  error that gets a test run before it can resolve anything.
- **Outcome:** pending — the corrected schedule has not yet been tested against
  a real arrival. Re-measure the yield at the next refresh; if it holds near 66%
  the projection stands.
- **Status:** Accepted

### D-59 — Pre-registration: intraday lead-lag, with a liquidity-matched control
- **When:** 2026-09-06T06:12:07-05:00
- **Decision:** Test whether a candidate's neighbourhood leads it at 1-minute
  resolution, on the 48 alert dates already in the extract. Design fixed **before
  any minute bar is fetched**:
  - **Neighbours are not re-selected.** Each candidate's top-20 comes from the
    daily pipeline, chosen on data ending strictly before the alert date. No
    intraday information enters neighbour selection.
  - **Measure.** Per candidate-date: `r_c(t)` = candidate 1-min log return,
    `r_n(t)` = equal-weighted mean of its neighbours' 1-min log returns.
    Cross-correlation `rho(k) = corr(r_n(t-k), r_c(t))` for k in -30..+30
    minutes, regular session only. Record `argmax_k rho(k)`.
  - **Hypothesis.** If information diffuses neighbourhood→candidate, the median
    `argmax` across candidate-dates is k > 0.
  - **Mandatory negative control.** Repeat with 20 random symbols drawn from the
    same universe, matched to that candidate's real neighbours on median dollar
    volume. Nonsynchronous trading alone makes liquid names appear to lead
    illiquid ones (Lo & MacKinlay), so an uncontrolled positive lag is
    uninterpretable.
  - **Symmetry check.** Also compute the reverse, `corr(r_c(t-k), r_n(t))`. A
    real diffusion effect is asymmetric; noise is not.
  - **Decision rule, pre-committed.** Claim intraday lead-lag only if the real
    median argmax exceeds the control's by >= 1 minute *and* a sign test across
    candidate-dates rejects at p < 0.05. Otherwise report null.
- **Why:** The alternative — fetch the bars and look — loses because this is the
  **third** interrogation of the same 102 signals (after D-31 and D-33), and the
  per-comparison false-positive rate is no longer the nominal one. Writing the
  rule down first is the only thing that keeps a positive result meaningful.
  Exploratory by construction: whatever it returns is a hypothesis for fresh
  data, not a confirmation on this data.
- **Outcome:** Ran as written on 1,066,454 minute bars over 83 candidate-dates.
  **Null**, by the pre-committed rule: real median argmax lag +0.0 min vs control
  +0.0 (needed >= +1.0), paired sign test 11 up / 20 down, p = 1.000. The real
  neighbourhood peaks at lag 0 on 95.2% of candidate-dates at median rho 0.674;
  the control peaks at lag 0 only 62.7% of the time at rho 0.238, i.e. the
  control's apparent lead-lag is noise wandering, exactly the microstructure
  artifact the control existed to expose. **But the result is uninformative
  about the hypothesis** — see D-60. 78.1% of neighbourhood slots are funds, many
  holding the candidate, so lag 0 was mechanically guaranteed.
- **Status:** Accepted — executed; superseded as evidence by D-60

### D-60 — 78% of every neighbourhood is a fund, and some of them hold the candidate
- **When:** 2026-09-06T06:16:52-05:00
- **Decision:** Record this as a validity defect in the neighbourhood definition
  itself, not merely in D-59's test. Re-run the intraday test with funds excluded
  from neighbour selection, under the same pre-committed rule. Whether the *daily*
  pipeline should also exclude funds is a separate decision and is **not** taken
  here.
- **Why:** Discovered while auditing D-59's null. Of 1,660 neighbour slots across
  all candidate-dates, **1,297 (78.1%) are funds** by the same name-based test
  D-43 already uses; 201 of the 388 distinct neighbours are funds. The most
  frequently selected are broad large-cap growth vehicles — TOPT, IVW, MGK, VUG,
  VOOG, SPYG, QQQM, IYW — every one of which holds the mega-cap candidates this
  feed alerts on. Correlating NVDA against VUG is not finding an informational
  neighbour, it is finding a mirror, and the worked AMZN example is worse: XLY
  and VCR are consumer-discretionary funds whose largest single holding is AMZN.
  So "seven neighbours moved down" is substantially "AMZN moved down, as
  reflected in funds that own AMZN". D-27 excludes the signal's own tickers and
  D-43 excludes leveraged/inverse products, but nothing excludes a plain index
  fund containing the candidate — that case was never considered.
  This is why D-59 returned a lag of exactly 0 on 95% of dates at rho 0.674: a
  fund's minute returns are contemporaneous with its constituents by
  construction. The test measured index arithmetic, not information diffusion.
  Not silently amending D-59 and re-running: that would be choosing the analysis
  after seeing the result. D-59 stands as executed and reported null; this is a
  separate pre-registration with one stated change.
- **Outcome:** Corrected test run on 1,174,267 minute bars, same 83
  candidate-dates, funds excluded from neighbour selection. **The null holds and
  now means something.** Real neighbourhood: 89.2% of candidate-dates peak at
  lag 0 (was 95.2%), median rho 0.505 (was 0.674) — removing funds lowered the
  contemporaneous correlation exactly as predicted, confirming they were
  inflating it, without changing the conclusion. Control: 51.8% at lag 0, rho
  0.226, mean lag +2.52 min, i.e. the uninformative pair keeps wandering.
  Pre-committed rule: gap +0.0 min (needed >= +1.0), sign test 16 up / 25 down,
  p = 1.000. **No intraday lead-lag.** Genuine non-fund peers move *with* the
  candidate at minute resolution, not before it, so the condition the pipeline
  looks for — neighbours moved while the candidate has not — barely occurs
  intraday. This is D-15's reasoning confirmed empirically rather than by
  citation, and it bears on the fact that the upstream trading is largely
  intraday.
- **Status:** Accepted

### D-61 — Keep the alert instant; `as_of` stays the anchor
- **When:** 2026-09-06T06:40:55-05:00
- **Decision:** `Candidate` gains `as_of_ts: datetime | None`, populated by
  `ExternalSignals` from the full `posted_at` value. `as_of: date` is unchanged
  and remains what every node reads. No node reads the new field yet.
- **Why:** The feed records alert times to the millisecond
  (`2026-05-29 14:34:10.936+00`) and ingestion threw them away at
  `posted_at[:10]`. That is unrecoverable data loss on a live feed — every day it
  stayed truncated was a day of intraday timing that could not be reconstructed
  later — and D-59/D-60 showed the pipeline cannot be pointed at minute bars
  without it, since a date anchors the graph at the first minute of the session
  and fails the trailing-window check.
  Widening `as_of` to a `datetime` instead lost decisively: `candidate_key` is
  `f"{symbol}|{as_of}"` with no time and no direction, so same-symbol
  same-day alerts deliberately collapse into one branch — 15 symbol/date pairs in
  the current baseline do, AMZN 2026-07-09 three times over. A timestamped key
  would split them and move every affected verdict, turning a data-preservation
  change into a silent behaviour change. Additive was the only option that keeps
  the guards meaningful.
- **Outcome:** Working. 102/102 candidates carry the instant; `candidate_key`
  still emits `TSLA|2026-05-29`; both baselines byte-identical to snapshots taken
  before the change (102 real, 6 synthetic); suite 42, ruff clean.
- **Status:** Accepted

### D-62 — Exclude every fund from neighbour selection, not just leveraged ones
- **When:** 2026-09-06T06:50:59-05:00
- **Decision:** `data/excluded-etfs.csv` now carries all 1,022 name-classified
  funds rather than the 185 leveraged/inverse products of D-43, which it strictly
  contains. `build_exclusions.py` defaults to the wide list;
  `--leveraged-only` restores the old behaviour. No code changed — the
  `excluded_symbols` path already existed and is already tested — so this is a
  configuration change, and `tests/test_exclusions_policy.py` pins the policy
  because a data file cannot otherwise fail a build. Answers Q-29.
- **Why:** D-60 measured 78.1% of neighbour slots as funds, dominated by broad
  large-cap vehicles holding the very mega-caps this feed alerts on. Correlating
  AMZN against XLY, whose largest holding is AMZN, is not evidence about AMZN.
  Keeping them lost on construct validity: the independence weighting (Q-12) can
  discount a correlated bloc, but it cannot repair evidence that is partly the
  candidate itself, and funds were crowding real names out of the top-20.
  **Measured effect:** 19 of 102 verdicts flip, neutral 78 → 62, corroborated
  9 → 21, contradicted 12 → 16, total effective evidence 37.5 → 64.3, rows with
  any evidence 21 → 38. Evidence *rises* because funds are mutually correlated
  and were being discounted to ~1/6 each; single names form smaller blocs. The
  worked AMZN 2026-07-24 case goes from six funds and GOOG at 1.543 effective, to
  GOOG, LYFT, ZG and Z at 3.000 — with Zillow's two share classes correctly
  weighted 0.5 each. Synthetic baseline unchanged, as it must be: no `SYN*`
  ticker is a fund, which makes it a clean control on the change.
  **What this is not:** evidence that the pipeline predicts better. D-31/D-33
  cannot judge it — see Q-31 — and modifying them to exclude funds would violate
  their pre-registration. The case for this change is construct validity alone.
- **Outcome:** Working, in the sense that the neighbourhoods are now defensible;
  predictive value untested and, at this n, untestable.
- **Status:** Accepted

### D-63 — Pre-registration: the shipped feature, on synthetic candidates, five years
- **When:** 2026-09-06T07:17:49-05:00
- **Decision:** Fixed **before any multi-year bar is fetched.** Answers Q-31.
  - **Feature: the shipped one, by construction.** The evaluator calls
    `retrieve_neighbourhood` → `leader_state` → `fuse_evidence` → `assess`
    directly. Nothing is reimplemented — that is the entire point, since D-31/D-33
    tested a proxy and were therefore silent about what ships.
  - **Candidates are synthetic.** No signal feed. For a sampled (symbol, date)
    pair, assess direction `"up"` and take the verdict. This tests the mechanism,
    not the trader, and so is not limited by the 102 alerts.
  - **Population.** Liquid non-fund single names from the existing universe
    construction, 2021-2026, sampled to ~20,000 (symbol, date) pairs across
    ~1,200 trading dates. Every date needs 60 prior sessions and 2 forward.
  - **Label.** 2-session forward return, **market-excess**: minus the
    equal-weighted mean return of the sampled universe that day. Raw returns
    would let a market rally masquerade as corroboration working.
  - **Primary test, one only.** Mean market-excess forward return, `corroborated`
    minus `contradicted`. **Standard errors clustered by date.** Rows sharing a
    date share a market shock, so unclustered SEs would treat ~1,200 effective
    observations as 20,000 and manufacture significance — the specific failure
    mode a large panel invites.
  - **Economic threshold, declared now.** A difference below **10bp** over two
    sessions is not meaningful regardless of p-value. At this n, statistical
    significance is cheap and must not be reported as a result on its own.
  - **Secondary, declared in advance so it is not a later fishing trip:** hit
    rate (excess return > 0) for the same two groups, and the `neutral` group's
    mean as a sanity anchor between them.
  - **Stated limitations, not discovered later.** The universe is selected on
    2026 liquidity, so the sample carries survivorship and look-ahead *in universe
    construction*; delisted names are absent. Single vendor. One asset class.
- **Why:** Q-31 established that the project's distinctive statistic —
  independence-weighted, direction-matched evidence — has never been tested,
  because both pre-registrations tested `max abs(neighbour z) - abs(candidate z)`
  instead. Waiting for the signal feed to reach power lost: D-58 prices that at
  14 months, and D-56 shows the feed is one author, so it would answer a narrower
  question anyway. The mechanism does not need the feed, and testing it on
  synthetic candidates is the only route to power that does not involve waiting.
  The cost is that a result here is about the mechanism in general, **not** about
  whether it corroborates this trader's picks — those remain separate claims.
- **Outcome:** **Executed. Null, and powered — the first informative null in
  this project.** n=19,867 over 1,194 sessions: corroborated −2.56bp, neutral
  −6.42bp, contradicted +2.02bp. Primary difference −4.57bp (wrong sign),
  clustered SE 6.39bp over 1,110 clusters, z=−0.72, 95% CI [−17.1, +7.9]bp,
  MDE 17.9bp against D-31's 100.6pp and D-33's 34.2pp. Leakage probe passed
  first: 25/25 verdicts unchanged when all prices from the assessment session
  onward are corrupted. A 400-pair smoke run had said **+114.90bp at z=+3.89**,
  monotonic — the full sample reversed it, which is the clearest possible
  argument for not stopping at a small sample you like. Survivorship does not
  explain it: by-year differences flip sign rather than decaying. See spike 14.
- **Status:** Accepted

### D-64 — Pre-registration: the same test, on the population the product serves
- **When:** 2026-09-06T12:38:57-05:00
- **Decision:** Re-run D-63 unchanged in every respect except the **candidate
  universe**, which becomes the top 100 non-fund names by median dollar volume.
  Neighbourhoods are still drawn from the full 2,183-name pool, as production
  does. Same market-excess label, same date-clustered SEs, same 10bp threshold,
  same 2-session horizon, same leakage probe. Answers Q-32.
- **Why:** Profiling the 97 profilable real alerts against 4,000 random
  (symbol, date) pairs on **own-name features only** — deliberately excluding
  anything about the neighbourhood, which would be circular — found them
  indistinguishable on every setup dimension: own 3-session |z| 0.504 vs 0.575,
  60-session volatility 0.023 vs 0.024, already-moving (|z|>=2) 5.2% vs 4.5%.
  The one difference is enormous: median dollar volume **$15.3B vs $61.8M**, the
  100th percentile. So "alert-worthy" does not need inventing, which was the
  whole risk in Q-32 — it means *mega-cap*, and nothing else measurable.
  That exposes a real limitation in D-63 rather than a flaw in it: it sampled
  uniformly from names with $62M median volume, so its powered null describes a
  population the product never sees. Defining the cut as the smallest round
  liquidity threshold covering **100%** of the 20 non-fund alert tickers gives
  top-100 (min $0.92B); top-50 covers only 85%. The cut is fitted to the alert
  population, never to an outcome.
  Declared in advance: 100 mega-caps are far more cross-sectionally correlated
  than 2,183 mixed names, so effective n will be materially below the row count
  even after date-clustering, and the MDE should be expected to be worse than
  D-63's 17.9bp despite an identical sample size. This is also the fifth
  interrogation of related data, so it is exploratory regardless of outcome.
- **Outcome:** **Null by the rule, but underpowered — as predicted in advance.**
  n=18,403 over 1,194 sessions: corroborated +4.28bp, neutral +4.64bp,
  contradicted −6.11bp. Difference **+10.39bp** — right sign, clears the 10bp
  economic threshold — clustered SE 8.11bp over 1,082 clusters, z=+1.28, 95% CI
  [−5.5, +26.3]bp. The SE *rose* from D-63's 6.39bp at the same row count,
  exactly the mega-cap co-movement declared beforehand, so MDE worsens 17.9 →
  22.7bp. This null rules out little; it neither excludes zero nor 26bp.
  **The important result is the power arithmetic, not the estimate.** Detecting
  10.4bp at 80% power needs SE ≤ 3.71bp, i.e. 4.8x more trading days ≈ 21 years.
  Alpaca's history begins in **2016**: ~10 years, giving SE 5.31bp and MDE
  14.9bp — still above the estimate. Add sector/beta residualisation cutting
  residual variance 50% and MDE lands at 10.5bp against an estimate of 10.4bp.
  **So Q-32 is not resolvable with this data source at this effect size**, by
  any amount of further compute or sampling. Recorded as a stopping condition
  rather than a to-do.
- **Status:** Accepted

### D-65 — At 120x leverage a 10bp underlying edge is ~4-6% of premium, not a rounding error
- **When:** 2026-09-06T14:09:44-05:00
- **Decision:** Treat D-64's unresolved +10.39bp as **economically material if
  real**, and reopening Q-32 with a deeper data vendor as a decision worth
  pricing rather than dismissing. Corrects a claim made in this session that the
  effect would be "small relative to the bid-ask on the options traded through".
- **Why:** That claim was asserted, not computed, and computing it reverses it.
  Across the 99 fires with usable premium and spot: median premium **$2.48**,
  median **7** days to expiry, median moneyness **1.033** (3% OTM), and median
  raw leverage `spot/premium` of **120x** (IQR 74-278x). A 10bp underlying move
  therefore lands at 4.19% of premium at delta 0.35 and 5.98% at delta 0.50.
  Typical bid-ask on short-dated single-name options is ~1-5% of premium, so the
  edge is the **same order** as the cost, not beneath it. Whether it clears them
  depends on execution, on realised delta for a 3%-OTM weekly (likely nearer
  0.25-0.35 than 0.50), and on theta over a 2-session hold — none of which this
  computes. The leverage cuts both ways: it amplifies dispersion and slippage as
  readily as edge, and 10bp is a mean difference, not a per-trade edge.
- **Outcome:** pending — no cost model, no fill data, and Q-32 itself remains
  unresolvable on Alpaca history (D-64).
- **Status:** Accepted

### D-66 — Pre-registration: the horizon ladder is a shape test, not a power play
- **When:** 2026-09-07T03:21:27-05:00
- **Decision:** Run the D-64 mega-cap design unchanged except the label, over
  horizons **2, 5, 10 and 21 sessions**, computed in a single pass so every
  horizon shares identical neighbourhoods and verdicts. Ten years of history
  (2016-2026, the maximum Alpaca carries). Standard errors clustered on
  **non-overlapping blocks of length = horizon**, because consecutive dates share
  all but one day of a 21-session forward return and date-clustering does nothing
  about that. The claim under test is the **shape** — whether the gap grows with
  holding period — not significance at any single horizon.
- **Why:** The motivation was that Cohen & Frazzini's 1.45% is *monthly* while we
  hold two days, so we may be exiting before the effect materialises. Computing
  power before running kills the hoped-for version of that: expected effect grows
  as `h` under linear accrual, while SE grows as `sqrt(h)` from return variance
  times `sqrt(h)` from losing blocks — also `h`. The ratio is invariant: 10.4/21.0,
  26/52.6, 52/105.2, 109/221, all 0.49. **Extending the horizon cannot increase
  statistical confidence**, by construction, and no horizon is powered. Running it
  anyway lost nothing and answers a different, useful question: the decay profile.
  If the gap is flat past two sessions, holding longer adds variance for no
  return; if it accrues, the trading implication is real even unproven; if it
  reverses, that is a reversal effect wearing a corroboration costume.
  Declared now so it cannot be claimed later: **no horizon in this ladder can
  produce a significant result**, and reporting one as such would be reading noise.
- **Outcome:** pending.
- **Status:** Accepted

### D-67 — News lands in Postgres, and the co-mention edge is economically real
- **When:** 2026-09-07T03:54:46-05:00
- **Decision:** Build `lagmatrix.news_article` / `news_symbol` / `news_coverage`
  plus a `news_comention` **view**, in a dedicated schema inside the existing
  `orchestrator` database. `scripts/load_news.py` backfills idempotently by
  (symbol, 90-day window). This is D-16's unbuilt half, finally built.
- **Why:** Parquet lost for this data: news is text with a many-to-many symbol
  tagging, queried by "which names appear together before date X", which is
  relational work. Postgres also makes it joinable to `audit_log`'s signals —
  hence a schema inside `orchestrator` rather than a separate database, since
  Postgres cannot join across databases. A separate database and a new k8s
  service both lost to a namespaced schema: purely additive, nothing existing
  touched, no new operational surface.
  `news_comention` is deliberately a **view, not a materialised table**: every
  caller must supply its own `created_at < as_of` bound, so look-ahead cannot be
  introduced by forgetting to filter. Materialising it would bake in one as-of.
  Bulk `COPY` through `ssh -> kubectl exec -i -> psql` rather than INSERT,
  because 5432 is a headless ClusterIP with no external route and per-row round
  trips over that path are unusable.
  **The edge is qualitatively different from correlation, which is the point.**
  On three months of NVDA/AMD news, NVDA's co-mention peers are AMD 44, MSFT 27,
  AVGO 22, MU 21, INTC 21, AAPL 17, DELL 14, AMZN 13, TSM 12 — foundry, memory
  supplier, customers, competitors. Correlation gave NVDY, DSI, QGRW, SPYG,
  SNPE, VOOG: six index funds. The fund contamination of D-60 cannot occur here,
  because nobody writes articles about VUG. This recovers the supply-chain
  structure D-16 abandoned for want of a data source, from a source we already had.
- **Outcome:** Schema live, loader verified idempotent (a re-run refetched 0 of 2
  covered windows). 152 articles / 797 tags / 205 symbols from a two-symbol
  three-month smoke test — 5.2 symbols per article, which is the co-mention
  density the edge depends on. First ten-year backfill completed at 32,213
  articles — and was **90%+ incomplete**, see D-69. Refetch in flight.
  **No predictive claim is made or implied**: this is a data asset, not a result.
- **Status:** Accepted

### D-68 — A coverage filter I wrote became a survivorship filter, and it manufactured an effect
- **When:** 2026-09-07T04:24:26-05:00
- **Decision:** Remove the global `closes.notna().sum() >= len(closes) * 0.9`
  column filter from `experiment3.py`. Coverage is enforced per trailing window
  inside the nodes, which is the point-in-time place for it. Any result from the
  first 10-year run (`data/results5.csv`) is void.
- **Why:** The 10-year run reported **+27.06bp, z=+4.53**, monotonic across all
  three verdicts — the first significant positive of the project, and false.
  It contradicted D-64's +10.39bp / z=+1.28, and the only intended change was
  history depth. The unintended change was the universe: that filter is applied
  over the **whole frame**, so lengthening the frame from 5 to 10 years silently
  required continuous trading since 2016. It dropped every post-2016 IPO — ABNB,
  PLTR, HOOD, COIN, CRWD, SNOW, UBER, AFRM, RKLB, SOFI, ASTS — and backfilled
  with decade-old names (CMCSA, PM, TJX, NEE, COP). 28 of 100 candidates differed.
  The diagnostic that settles it: on the **identical** 2021-2026 dates, D-64's
  universe gives +10.4bp and the decade-survivor universe gives **+41.1bp**. Same
  dates, same code, same design — only the survivor selection differs, and the
  effect quadruples. Effect size scaling with the degree of survivor selection is
  the signature of the bias, not of alpha.
  Tuning the threshold lost to deleting it: any global coverage rule is universe
  selection with hindsight, and `graph_retriever` already applies
  `pool.notna().sum() >= trail * 0.8` inside each point-in-time window.
- **Outcome:** Clean re-run done. The filter was inflating the estimate
  (+27.06 → +17.95bp) but was not the whole story — see D-71, which shows the
  residual is heterogeneity, not effect.
- **Status:** Accepted

### D-69 — The news backfill captured 3% of the articles, biased to the earliest
- **When:** 2026-09-07T04:34:43-05:00
- **Decision:** `WINDOW_LIMIT = 10_000` replaces `PAGE_LIMIT = 50` in
  `load_news.py`, and the hand-rolled `next_page_token` loop is deleted. Coverage
  rows with `n_articles >= 50` are invalidated and refetched; the 280 rows below
  50 are genuinely complete and kept.
- **Why:** The first completed backfill reported 32,213 articles and looked
  healthy. It was not: **no window ever exceeded 50 articles and 708 hit exactly
  50**, which is 41 windows x 50 = the 2,050 that TSLA, SPY, NVDA, NFLX and MU
  each reported to the row. `alpaca-py`'s `get_news` paginates *internally* up to
  `NewsRequest.limit` and returns `next_page_token=None` regardless, so the manual
  token loop could never advance and `limit=50` was a hard per-window ceiling.
  Two things made it worse than a simple undercount. With `sort="asc"` the 50
  kept were the **earliest** in each 90-day window, so the sample is
  systematically biased in time rather than merely thin. And the true volume is
  an order of magnitude higher: TSLA alone has 497 articles in January 2024, and
  2024-2026 yields 12,146 against the ~550 the broken version produced — 22x.
  Caught by noticing that five different tickers reported the identical round
  number. A per-symbol total that looks plausible can hide a per-window cap;
  the coverage table is what made it checkable, which is the argument for having
  recorded `n_articles` per window rather than just "done".
- **Outcome:** Refetched. 229,737 articles / 932,545 tags / 16,058 symbols,
  2014-11-07 → 2026-09-07, 248 MB. Max window 4,772 against a 10,000 ceiling, so
  nothing truncated; no retries needed. 7x the broken version's 32,213.
- **Status:** Accepted

### D-70 — Co-mention needs a breadth cutoff and PMI, or it is a popularity ranking
- **When:** 2026-09-07T04:56:53-05:00
- **Decision:** The co-mention edge is defined as **PMI over articles tagging
  <= 8 symbols**, not raw co-occurrence counts. Answers Q-14.
- **Why:** Two distinct failures, both measured on the full 229,737-article corpus.
  **Hub noise.** Pairs scale as n(n-1)/2, so breadth-heavy articles dominate
  combinatorially: the median article tags 2 symbols and the largest tags 2,649,
  and the 2.1% of articles tagging more than 20 symbols contribute **95.2% of all
  co-mention pairs**. An unfiltered graph is 95% market-wrap.
  **Popularity.** Even after filtering breadth, raw counts rank NVDA's peers as
  AAPL, MSFT, TSLA, META — not suppliers, merely the other most-covered names.
  Raw co-occurrence measures base rate; PMI divides it out.
  With both applied, NVDA's top peers become CRWV, ARM, SMH, MRVL, SFTBY, TSM,
  SOXX, DELL, CSCO, AMD, BIDU, BABA, ORCL, SSNLF — GPU-cloud customer, attempted
  acquisition target and its owner, foundry, competitors, server and cloud
  customers, memory supplier. That is the economic structure D-16 abandoned for
  want of Compustat.
  The cutoff at 8 is p90 of the breadth distribution, chosen as a distributional
  landmark rather than tuned against any outcome — no return data was consulted.
  Sector ETFs (SMH, SOXX) survive and should be removed by D-62's fund list.
  **Still descriptive.** Three measures over identical data give three different
  graphs — funds, popular names, business relationships — and the third merely
  looks the most sensible. The correlation edge also looked sensible before it
  was a powered null (spike 14). No predictive claim until one is pre-registered.
- **Outcome:** pending — no return test has been run against this edge.
- **Status:** Accepted

### D-71 — The estimate is 3x more variable across years than its errors allow
- **When:** 2026-09-07T05:35:38-05:00
- **Decision:** Treat the whole synthetic-candidate line of tests as **null**.
  Date-clustered standard errors are insufficient; report inverse-variance
  pooling across years, or a heterogeneity-inflated error, not the naive figure.
- **Why:** With D-68's survivorship filter removed the 10-year run still showed
  +17.95bp at z=+2.55, which looked like a real result surviving a bug fix. It is
  not. Three independent checks agree:
  **The sign flips by period.** 2016-2020 gives −17.81bp (z=−2.27), 2021-2026
  gives +40.08bp (z=+3.89). By year, 2018 is −50bp (z=−3.99) and 2024 is +99bp
  (z=+4.29). Both cannot be true.
  **It is not robust to universe construction.** On identical 2021-2026 dates,
  D-64's universe gives +10.4bp and this one +40.1bp — a 4x difference from a
  defensible change in how the candidate list is built, with no return data
  involved in either choice.
  **The errors are quantifiably too small.** Cochran's Q = 47.4 on 9 df where ~9
  is expected; I² = 81%; the estimate's year-to-year SD is 51.6bp against a
  median within-year SE of 17.2bp — 3.0x more movement than sampling allows.
  Inverse-variance pooling across years, which weights each year by its own
  precision instead of equal-weighting observations, gives **−2.31bp**.
  The cause is regime-level serial correlation: date-clustering absorbs
  same-session shocks but nothing across adjacent days within a regime, and the
  feature's verdicts are themselves persistent. Every "significant" result in
  this line — +115bp at n=122, +64bp in one evidence band, +27bp under the
  survivorship filter, +17.95bp here — has dissolved on a check the previous one
  did not have. That pattern is itself the finding.
- **Outcome:** Working as a correction. D-63's original powered null stands; D-64
  and this run add no evidence against it.
- **Status:** Accepted

### D-72 — Directed EDGAR edges: the data supports them, regex extraction does not
- **When:** 2026-09-07T06:53:46-05:00
- **Decision:** Build `lagmatrix.filing_mention` / `filing_coverage` and a
  `supply_edge` view from 10-K text, storing the **passage verbatim** alongside a
  provisional `relation` label marked `confidence='heuristic'`. Labelling is
  explicitly a separate, replaceable step. Reverses D-16's abandonment of the
  supply-chain graph.
- **Why:** D-16 dropped this for "no free substitute" for Compustat. EDGAR is
  free and reachable, so the premise was wrong — but the data has a shape that
  had to be measured before designing anything.
  **Large filers anonymise.** NVDA: "one direct customer represented 22% of total
  revenue"; AVGO and MU likewise. ASC 280 compels the magnitude, never the name.
  **Suppliers name.** 14 of 20 sampled suppliers name a large customer — QRVO
  names Apple at 50%, SWKS at 82%, AMKR at 72%, CRUS "one end customer, Apple
  Inc." So the edge is built from the small end pointing up, which is also the
  direction Cohen & Frazzini found predictive, and it is genuinely **directed**:
  QRVO names Apple, Apple never names QRVO. Correlation is symmetric and
  co-mention undirected, so this is the first edge in the project that can
  express a lead-lag hypothesis at all.
  **Regex extraction is the wrong instrument, and I proved it the slow way.**
  Six tuning passes, each trading precision against recall: sentence boundaries
  break on "Inc."; a length-sorted vocabulary truncated at 6,000 silently dropped
  every *short* name, so "Apple" could never match; company short-names collide
  with ordinary words (MicroStrategy renamed itself "Strategy"; Celsius Holdings
  vs "degrees Celsius" in a chip filing); and a competitor veto scanned over
  ±420 chars rejects nearly everything because 10-Ks say "competitive" on every
  page. Final state is high precision, poor recall, with a known error class
  (CRUS→GFS is a foundry read as a customer). That oscillation is the argument
  for an LLM pass, which is the job D-16 reserved for it and which cannot run
  here — no `ANTHROPIC_API_KEY` in this environment.
  Hence the design: the passage is the durable artefact, the label is disposable.
  Re-labelling never re-crawls EDGAR, and every edge stays auditable against the
  filing that produced it.
- **Outcome:** Deep crawl done — 5,880 filings, 1,000 filers, 8 filings each,
  **0 fetch failures** after making the crawl concurrent (6 workers behind a
  shared 8 req/s token bucket) and batching writes 40 filings at a time.
  **1,211 edges, 172 suppliers, 137 counterparties, 239 distinct pairs, spanning
  2018-10-19 → 2026-08-20** at 126-168 edges per year — a point-in-time series,
  not a snapshot, which is what a backtest needs.
  **Precision ~9/12** on a random audit. The correct ones are textbook: KMB→WMT
  ("our largest customer, Walmart Inc., represented approximately 13 percent"),
  CAG→WMT (28%), AMGN→COR (McKesson/Cencora/Cardinal, each >10%). The three
  failures have distinct causes, all of them LLM-fixable: an industry statistic
  ("the Baker Hughes Land rig count increased 52%" → SEI→BKR), a generic-term
  collision (Quantum Computing Inc. matching "Quantum Computing as a Service"
  → RGTI→QUBT), and a competitor list that slipped the proximity veto
  (APTV→TEL, among Leoni/Molex/Sumitomo/Yazaki).
  **Coverage is the binding limit, not precision.** Only **8 of the 24 alert
  tickers** are reachable as customers, and only AAPL has depth: AAPL←13
  suppliers, AVGO/GOOGL/META←3, TSLA/NOW/SMCI←2, PLTR←1. So this edge can
  support a test on Apple's supply chain and essentially nothing else in the
  signal universe.
  Also corrects a number in this entry's own Why: "14 of 20 suppliers name a
  customer" came from a sample I hand-picked *because* they were concentrated
  semiconductor suppliers. Across a broad cross-section it is ~13%, flat across
  liquidity ranks (15%/16%/9%/15% for ranks 1-50/51-100/101-175/176-250). Most
  companies simply have no single >10% customer to disclose.
  **Superseded by a full-population audit (2026-09-07).** The "~9/12" above was
  12 rows read by hand. Classifying all 1,211 stored passages by the sentence
  that actually names the counterparty gives **69.2% sound** (838), and names the
  three failure modes rather than sampling them: 20.9% (253) name the party but
  state no relation — this is where CRUS→GFS lives, "wafers primarily *supplied
  by* GLOBALFOUNDRIES", a reversed relation the active-voice veto never saw;
  5.5% (66) competitor lists; 3.7% (45) acquisitions; 0.7% (9) explicitly
  reversed. GOOG→GOOGL is in there too — a share class, not a counterparty.
  This is the re-labelling D-72 reserved, and it needed no re-crawl. See D-78.
- **Status:** Accepted

### D-73 — Pre-registration: does Apple's move lead its suppliers? (Q-34)
- **When:** 2026-09-07T11:52:18-05:00
- **Decision:** Fixed before any relationship is examined. Only marginal
  quantities — return variances — were computed to size the test.
  - **Direction: customer → supplier**, the documented one. Cohen & Frazzini's
    mechanism is that investors watch the large customer and fail to update the
    small supplier, so AAPL's move should lead its suppliers, not the reverse.
    Note this is *signal generation on suppliers*, not corroboration of an Apple
    alert — a different product from the one D-18 describes, and worth saying.
  - **Portfolio, point-in-time.** Equal-weighted across Apple suppliers, where a
    supplier joins only on or after the `filing_date` of the 10-K that names
    Apple. Membership grows 1 → 13 over 2018-10-19 → 2026-09-04. Using today's
    supplier list on 2019 prices would be exactly the look-ahead the
    `news_comention` view was designed to prevent.
  - **Primary test, one only.** OLS slope `b` in
    `supplier_excess(t+1) = a + b · AAPL_excess(t)`, both market-excess against
    the equal-weighted universe. A one-session lag, so windows do not overlap and
    no autocorrelation correction is needed — chosen for that reason.
  - **Economic threshold, declared now: |b| >= 0.02.** Below that a 1% Apple move
    implies under 2bp on the supplier portfolio, which is not tradeable whatever
    the p-value.
  - **Mandatory secondary, from D-71's lesson:** the same slope estimated per
    year, with Cochran's Q and I². D-71 showed date-clustered errors understate
    uncertainty ~3x when regime heterogeneity is present. A pooled slope that
    is not stable across years is not a result.
  - **Declared power.** Supplier-portfolio daily excess return sd is 156bp over
    1,979 sessions; MDE on the mean is 9.8bp at h=1, 19.7bp at h=2, 49.2bp at
    h=5. Cohen & Frazzini's 145bp/month is ~7bp/day, so **h=1 is the only
    horizon where the expected effect is near the detectable one**, and even
    there it is marginal. h=2 and h=5 are pre-emptively excluded rather than
    tried and discarded.
- **Why:** Q-34 exists because correlation is symmetric and was a powered null
  (spike 14), and co-mention is undirected. This is the first directed edge the
  project has had. Testing it now, narrow, beats widening the crawl first:
  D-72 established only 8 of 24 alert tickers are reachable and only Apple has
  depth, so more crawling buys coverage of names that are not in the signal
  universe. Building more graph before testing the one in hand is how the
  correlation edge consumed four months.
- **Outcome:** **Null, and — for once — stable.** n=1,978; b=+0.0090, SE 0.0195,
  z=+0.46, CI [−0.029, +0.047]; a 1% Apple move implies +0.9bp on the supplier
  portfolio. Below the declared 0.02 threshold and not significant.
  The heterogeneity check passes for the first time in this project:
  **Cochran Q = 7.0 on 7 df, I² = 0%**, yearly slopes scattered around zero with
  a single 2019 outlier. Contrast D-71, where I²=81% and the sign flipped between
  periods. So the estimate itself is trustworthy — this is not a specification
  artefact.
  **But it is an underpowered null, not a powered one, and that must not be
  overstated.** MDE is 0.0546 against a declared economic threshold of 0.02, so
  a tradeable slope could exist unseen; the CI reaches +0.047. n=1,978 already
  uses every available session and the edges only begin in 2018, so more compute
  cannot fix it. Superseded as evidence by D-74, which pools across chains —
  which is what Cohen & Frazzini actually did.
- **Status:** Accepted — extended by D-74

### D-74 — Pre-registration: pool the same test across every supply chain
- **When:** 2026-09-07T11:53:31-05:00
- **Decision:** Repeat D-73 unchanged in every respect except the population:
  all customers with at least 3 distinct suppliers, each customer contributing
  one point-in-time equal-weighted supplier portfolio, pooled into a single
  panel regression of `supplier_excess(t+1)` on `customer_excess(t)`. Same
  one-session lag, same market-excess construction, same 0.02 economic
  threshold, same mandatory per-year heterogeneity check.
  **Errors clustered by date**, because on any given session every chain shares
  the same market shock — D-71's lesson, applied in advance rather than
  discovered afterwards.
- **Why:** D-73 returned a stable null but could not have detected an
  economically meaningful slope: MDE 0.0546 against a 0.02 threshold. The cause
  is not the design but the population — one customer's suppliers is a noisy
  object, and Cohen & Frazzini's 145bp/month is a portfolio across *many* pairs,
  not one chain. Pooling is therefore the properly powered form of the identical
  question, not a new hypothesis, and it is the only lever available: n=1,978
  already exhausts the sessions and the edges start in 2018.
  Declared in advance: pooling multiplies observations but **not** independent
  information, since chains co-move. Expect the date-clustered SE to fall far
  less than sqrt(number of chains) would suggest, and report the effective
  cluster count alongside the slope. If the clustered MDE still exceeds 0.02,
  the honest conclusion is that this edge is untestable with what EDGAR yields,
  not that it is absent.
- **Outcome:** **Null, stable, still underpowered — and the pre-declared warning
  held.** 18 chains, n=28,131 chain-days over **1,978 date clusters**. Pooling
  moved the SE 0.0195 → 0.0120, a factor of **1.6 rather than sqrt(18)=4.2**,
  exactly because chains co-move; writing that down first is what stopped 28k
  "observations" reading as power they never had.
  b = +0.0052, clustered SE 0.0120, z=+0.43, CI [−0.018, +0.029]; a 1% customer
  move implies +0.5bp on its suppliers. Inverse-variance pooled slope is
  **+0.0000**, Cochran Q = 5.3 on 7 df, **I² = 0%**, yearly slopes scattered
  symmetrically about zero. This is the cleanest estimate the project has
  produced — no instability, no specification sensitivity, no artefact.
  **MDE 0.0337 still exceeds the 0.02 threshold**, and the CI contains 0.02, so a
  tradeable slope cannot be excluded. The ceiling is structural: 1,978 clusters
  is every session the edges span, Alpaca's prices start 2016 so deeper EDGAR
  history buys at most 1.26x, and more chains yield ~1.6x per 18. Resolving this
  needs a different data regime, not more work in this one — the same wall D-64
  hit.
- **Status:** Accepted

### D-75 — Pre-registration: the co-mention edge, and what it can and cannot answer
- **When:** 2026-09-07T12:19:29-05:00
- **Decision:** Mirror D-74's design exactly, changing **only the edge**:
  neighbours are the top-20 PMI peers over articles tagging <= 8 symbols (D-70),
  recomputed from articles strictly before each month, seeds are the 24 alert
  tickers, one-session lag, both sides market-excess, errors clustered by date,
  and the same mandatory per-year heterogeneity check.
  **Declared before running: this test cannot resolve the 0.02 economic
  threshold.** Detectable effects are |b| >= ~0.06. It is run to bound a *large*
  effect, not to establish a tradeable one, and a null must be reported as
  "no large effect" rather than "no effect".
- **Why:** Co-mention is the last untested edge, and it is not the same object as
  correlation despite both being undirected. Correlation selects for
  contemporaneous co-movement, which is why D-59/D-60 found lag 0 almost
  tautologically. Co-mention selects for shared news attention, and two
  co-mentioned firms need not co-move at all — so it can carry information
  correlation structurally cannot.
  Power, computed from marginal variances only: single-seed slope SE 0.0345;
  pooling 17 seeds buys ~1.6x (the factor D-74 *measured*, not sqrt(17), because
  seeds co-move), giving ~0.0216 and MDE ~0.060.
  The orientation is the reason it is worse than D-74's 0.0120, and it is
  **forced**: D-74 regressed a supplier *portfolio* (sd 156bp) on a customer
  *single stock*, whereas the co-mention hypothesis is that neighbours lead the
  candidate, so the candidate — sd 244bp — must be the dependent variable and the
  low-variance portfolio the regressor. A noisy dependent with a quiet regressor
  is the worst arrangement for identification, and no reformulation preserves the
  hypothesis.
  Running it anyway beat the alternatives. Skipping loses the only bound
  obtainable on the last edge. Backfilling news for 100+ seeds to reach MDE ~0.03
  costs hours and still would not clear 0.02, so it is not the lever it appears
  to be. Reporting a null here as evidence of absence would be the actual error,
  which is why the limit is written down first.
- **Outcome:** **No large effect, exactly as bounded in advance.** n=29,014
  seed-days over 2,411 date clusters, 18 seeds, neighbours recomputed monthly
  from articles strictly prior. b=+0.0013, clustered SE 0.0187, z=+0.07,
  CI [−0.035, +0.038]; a 1% neighbour move implies +0.1bp on the candidate. MDE
  landed at 0.0524 against the ~0.06 predicted, so effects above ~0.04 are
  excluded and the 0.02 threshold is not resolved — as declared.
  Heterogeneity is low: Q=10.3 on 9 df, **I²=12%**.
  **One diagnostic worth keeping.** The inverse-variance pooled slope reads
  −0.0247 against a naive +0.0013, and that is an artefact: 2021's clustered SE
  is 0.0178 against 0.04-0.10 elsewhere, so it carries **53% of the pooled
  weight**, and dropping that single year flips the pooled estimate to +0.0028.
  The primary clustered estimate is the sound one. This is the *opposite*
  diagnosis from D-71 — there I²=81% and pooling was the correction; here I² is
  low, so pooling adds nothing and merely concentrates weight on whichever year
  had the most regressor variance. The same statistic can be the fix or the
  artefact depending on the heterogeneity it is applied to.
- **Status:** Accepted

### D-76 — Measure the signal, not just the graph: the feed earns, unprovably
- **When:** 2026-09-07T13:23:34-05:00
- **Decision:** Record realized P&L from broker fills as a first-class result,
  and treat the index-ETF leg as the one actionable finding. `scripts/realized_pnl.py`
  reconstructs round-trips from `EntryFilled` / `PartialExitFilled`.
- **Why:** Every experiment here asked whether the *graph feature* predicts; none
  asked whether the *signal* does, and the fills were in `audit_log` throughout.
  77 real-money round-trips: **+$37,586**, mean +5.9%, win rate 68.8%, median hold
  22.4h. Three qualifications, each measured:
  **Five trades from breakeven.** Top 3 = 86% of P&L, top 5 = 115%; without them
  the account is −$5,634. Those five are NVDA/MU/NVDA/MU/AMD at +79% to +101%,
  on positions 2.1x typical — concentration in *return*, not sizing.
  **Not distinguishable from zero.** SE 4.5%, t=+1.31, CI [−2.9%, +14.7%]. A
  payoff ratio of 0.70 means it survives only while the 68.8% win rate does.
  **Index ETFs lose systematically**: SPY/QQQ, 30 trades, −5.9% mean, −$6,867,
  against single names at +13.4% and +$44,454. That is the one pattern not
  carried by outliers, and it is 39% of all activity.
  This also undercuts D-29's choice of the underlying's forward return as the
  label: D-33 measured that at −0.86% with a 41.8% hit rate over 2 sessions,
  while the options were profitable over the same feed. Leverage and a 22-hour
  hold mean the underlying return is not what the account earns, so every
  experiment scored against it has been measuring an adjacent quantity.
- **Outcome:** Working — 217 round-trips reconstructed (77 real, 140 paper),
  written to `data/realized-pnl.csv`. See spike 15.
- **Status:** Accepted

### D-77 — Return to the original goal: a LangGraph + graph/vector showcase
- **When:** 2026-09-07T17:08:11-05:00
- **Decision:** Close the research thread and build the showcase the project was
  started for. Implement `adapters/arango.py` and `adapters/vector.py` against
  the corpora the research produced, honour `max_lag_hops` with real multi-hop
  traversal, and surface both in the existing live web UI. Partially supersedes
  D-16.
- **Why:** Raised by the owner, and correct. The drift is traceable to two
  decisions four hours apart on 2026-09-03. **D-16** ("graph edges are derived,
  not sourced") removed the reason for a graph database to exist — once edges are
  a pandas `corrwith` over Alpaca bars, ArangoDB stores nothing that is actually
  a graph. It was decided for point-in-time correctness and single-vendor
  reproducibility, both sound, and the cost to the stated goal went unnoticed.
  **D-26** then made oh-my-tradeagent's `audit_log` the evaluation set — which
  the owner asked for — and from there the work became validation. `arango.py`
  and `vector.py` have been 21 and 14 lines of `NotImplementedError` for the
  entire project, including after the owner restated the portfolio goal.
  Reversing D-16 *now* rather than then is the right order, and not merely a
  rationalisation: in September the graph store would have held derived
  correlation edges, which is a graph database holding something that is not a
  graph — a weak demo, and probably why it kept being deferred. The research
  detour produced the assets that make it real: a 932,545-tag PMI-weighted
  co-mention graph (D-67, D-70), 1,211 **directed** dated supply-chain edges
  (D-72), and a 229,737-article text corpus. Those are genuinely graph- and
  vector-shaped; correlation neighbourhoods never were.
  Continuing the research lost on evidence: three edge types are null (spike 14),
  two tests sit at the resolution limit of free data, and the literature explains
  why — every documented cross-firm effect needs illiquid names or long horizons,
  and this feed trades neither.
- **Outcome:** pending — plan being written to `docs/plans/`.
- **Status:** Accepted — partially supersedes D-16

### D-78 — Classify the relation from the naming sentence, not the ±420-char window
- **When:** 2026-09-07T17:55:25-05:00
- **Decision:** Derive `relation` from the single sentence that names the
  counterparty, with an explicit precedence — competitor > corporate_action >
  reversed > customer > unstated — and build `supplies_to` edges from the
  `customer` label alone. The percentage is read from that same sentence. Six
  labels replace the one hardcoded `'customer'`; `passage` is untouched, so this
  is a re-read of stored text and re-crawls nothing.
- **Why:** The showcase page shows a reviewer the verbatim filing sentence behind
  each edge, and that panel is what exposed this: `LITE→AVGO` would have rendered
  "we compete against various companies" beneath a *supplies* label. The
  alternative was to ship all 1,211 edges with an honest accuracy caveat. Rejected
  — a caveat does not survive contact with a reader who can click the edge and
  read the contradiction, and the goal's whole premise is that the graph is shown
  working rather than described as working. Window-scoped matching is what failed:
  a ±420-char window spans several sentences, so a competitor list two sentences
  away from a customer mention scored as a customer. One sentence cannot say both.
- **Outcome:** **Applied to real data.** 1,211 rows rewritten in one transaction:
  818 customer, 288 unstated, 47 competitor, 46 corporate_action, 12 reversed.
  `supplies_to` reloaded at 818 edges / 514 vertices. Q-35 then re-ran D-74's
  pooled test on the cleaned graph: **b = -0.0015, I² = 44%** (was +0.0000, I² 0%)
  — still a null far below the declared 0.02 threshold, so the conclusion holds
  while its inputs no longer do. A cost this also surfaced: every 2-hop supply
  path vanished with the bad edges, so supply-chain multi-hop was an artifact of
  competitor lists and the "Baker Hughes rig count" statistic.
- **Status:** Accepted

### D-79 — The candidate is the leader, not the lagger: `leaders_of` dropped for `laggers_of`
- **When:** 2026-09-07T18:05:00-05:00
- **Decision:** `graph_retriever` calls the already-declared
  `laggers_of(leader, max_hops, as_of)`, walking **INBOUND** from the candidate
  to its suppliers. The planned `leaders_of` / `OUTBOUND` pair is dropped before
  it was written. On the returned `LagEdge`, `leader` is the candidate and
  `lagger` is the neighbour reached.
- **Why:** PLAN-2026-09-07 TASK-3.1 assumed "a candidate is a potential lagger"
  and added `leaders_of` to find what leads it. That premise is false for this
  candidate set, and D-72 already contains the measurement that refutes it:
  "only 8 of the 24 alert tickers are reachable **as customers**." Every real
  candidate — AAPL, AVGO, TSLA — sits at the customer end. Combined with D-73's
  fixed direction (customer → supplier, Cohen & Frazzini), the candidate is a
  **leader** and its suppliers are its **laggers**. `leaders_of(AAPL)` would ask
  for Apple's customers, which barely exist in this graph; the traversal that
  produces the 13 suppliers is `laggers_of`. The alternative — keep `leaders_of`
  and walk OUTBOUND — was defended on D-73's economics, which are correct but do
  not rescue the premise: getting the direction right while putting the candidate
  on the wrong end still inverts the query. Surfaced by the `red-adapters` agent,
  which noticed that `scripts/capture_showcase.py` had been running INBOUND
  against real data all along while the plan's worked example said OUTBOUND.
  Test isolation landed at the **database** layer, not the collection layer: the
  fixtures create disposable `test_arango_topology` / `test_vector_index`
  databases holding production-named `equity`/`supplies_to`/`article`, so the
  adapters hardcode those names and take only `db`. Collection-name constructor
  kwargs were tried first and abandoned — they put a parameter on the production
  API whose only purpose was to make a test pass (CLAUDE.md §2), and they forced
  test collections into the live `lagmatrix` database, which promptly produced
  real races between concurrent runners (`IndexCreateError: index was dropped`,
  `DocumentInsertError: conflicting key: chip-article`). A separate database
  cannot collide with production data at all. Reaching that took five reversals
  across two agents and me, most of them caused by my own crossed messages.
- **Outcome:** **Done and verified against the live instance**, not on report:
  6 passed (4 topology + 2 vector) with `LAGMATRIX_ARANGO_URL` tunnelled to the
  homelab ArangoDB 3.12.11; full suite 73 passed + 6, ruff clean; production
  `equity`/`supplies_to`/`co_mentioned`/`article` confirmed at 514/818/2129/47640
  afterwards with no leftover test collections. `laggers_of` implements the
  ALL-quantified per-path guard and picks the shortest path, mirroring
  `capture_showcase.py`. Two real defects surfaced *because the next agent in the
  chain refused to guess* rather than from the tests: the plan's inverted premise,
  and fixtures that seeded collections the adapter could not reach — the latter
  hidden underneath a `TypeError` that looked like a complete explanation.
- **Status:** Accepted

### D-80 — The comparison bar: FalkorDB's GraphRAG app, and the one element we were missing
- **When:** 2026-09-07T18:40:00-05:00
- **Decision:** Treat FalkorDB's GraphRAG showcase as the reference page the goal
  demands, and adopt its most persuasive device: a **side-by-side where vector
  retrieval visibly fails and the graph visibly succeeds**, on the same question,
  with both halves real output. For LagMatrix that is TSLA — semantic search over
  47,640 articles returns CNBC "Final Trades" noise, while the traversal returns
  APTV at 9% of net sales and JBL, each with the verbatim 10-K sentence.
- **Why:** The goal says success is measured by comparison, not self-assessment,
  so the bar had to be an actual page. FalkorDB's makes four things visible:
  SSE-streamed pipeline stages, an "explainability subgraph" (source document →
  chunks → entities → answer), a clickable force-directed explorer, and a
  side-by-side where vector RAG hallucinates three non-existent World Cup host
  cities while GraphRAG lists all 22 finals correctly. We already had analogues
  of the first three — `scripts/serve.py` streams a real `graph.astream` over
  SSE, D-78's sentence-level provenance is a stronger explainability chain than
  theirs (a dated SEC filing, not an LLM-extracted chunk), and the page draws the
  traversal. **The contrast demo is the one we lacked**, and it is the element
  that does the actual persuading, because it is the only one that shows the
  graph doing something the vector index cannot. The alternative — asserting in
  prose that the graph adds value — is exactly the "read about it rather than see
  it" failure the goal names.
  Two things we have that the bar does not, and should therefore lead with rather
  than bury: **point-in-time correctness** (an `as_of` that provably changes the
  result, which no LLM-extraction demo attempts) and **honest negative results**.
- **Outcome:** The contrast is real and, usefully, **it cuts both ways** — which
  is a better demo than FalkorDB's, whose side-by-side only ever shows the graph
  winning. Measured on the current trace:
  **AAPL** — graph returns 12 named suppliers with disclosed percentages (AMKR
  27.7%, QRVO 50%, AVGO 25%) in 6 ms; vector returns "ISM Manufacturing Prices
  For February 70.5 Vs 60.6 Est." at 0.411. Graph wins decisively.
  **TSLA** — graph returns APTV at 9% of net sales; vector returns "Amazon,
  Alphabet, KLA And A Health Care Stock On CNBC's 'Final Trades'". Graph wins.
  **AVGO** — graph returns **nothing** (every AVGO edge was a divestiture or a
  competitor list and was dropped by D-78); vector returns Arista on supply
  shortages and ASML on EUV demand, both genuinely on-topic. **Vector wins.**
  Shipping the case the graph loses is the point: a demo that admits a failure
  mode is more credible than one that cannot, and this one costs nothing because
  the honest answer — use both, they fail differently — is also the correct one.
- **Status:** Accepted

### D-81 — Two edge types, opposite orientations: only the candidate's *laggers* count as evidence
- **When:** 2026-09-08T00:10:00-05:00
- **Decision:** `context_fusion` builds `leader_move` evidence only from edges
  where the candidate is the **lagger** (`e.lagger == c.symbol`). Supply-chain
  edges, where the candidate is the leader, contribute none. They stay in
  `lag_edges` and stay on the showcase page; this governs what counts toward a
  verdict, not what is retrieved or displayed.
- **Why:** PHASE-4 made `graph_retriever` additive — correlation edges plus
  ArangoDB supply edges — and the two carry **opposite orientations relative to
  the candidate**. A correlation edge has `leader`=neighbour, `lagger`=candidate.
  A supply edge (D-79) has `leader`=candidate, `lagger`=supplier. `fuse_evidence`
  read `[e.leader for e in ...]` assuming every leader is a neighbour, so the
  candidate's own symbol entered that list once per supplier — AAPL twelve times
  — duplicating DataFrame columns until `rho[m]` returned a Series and
  `int()` raised. Deduplication would have silenced the crash while leaving the
  real error: counting a supplier's move as evidence about its customer inverts
  the inference D-73 fixed. D-73 already said so — the supply edge is "signal
  generation on suppliers, not corroboration of an Apple alert" — so the edge
  genuinely cannot corroborate a mega-cap candidate, and the honest wiring says
  that rather than manufacturing evidence from it.
  **Found only by running the real pipeline.** Every unit test passed; the
  showcase capture crashed on its first real `graph.astream`. The old capture
  synthesised its node timeline with a `mark()` helper, so it would have produced
  a clean-looking page from a pipeline that could not complete a single run.
- **Outcome:** Fixed with a one-line filter plus a comment naming this entry.
  80 passed, `check_baseline.py --synthetic` unchanged. Verified the fix is not a
  behaviour regression by running the same three candidates with and without
  `arango_topology`: both give `effective_evidence 0.0` on 2026-06-01, so the
  earlier inflation only arose where a self-shock coincided with a supply edge.
  `scripts/capture_showcase.py` now runs a real `graph.astream` and the trace is
  observed rather than synthesised: `Send` fan-out, `leader_state` and
  `vector_retriever` landing at identical `t_ms` (genuinely parallel), the
  rejoin at `context_fusion`, and — with `halt_on_contradicted=True` on
  **2026-05-11** — `__interrupt__`, the `review` gate firing on TSLA, and the
  resume through to `publisher`. That date was chosen because it is the only
  sampled one producing all three verdicts (AAPL neutral, AVGO corroborated,
  TSLA contradicted); the interrupt had never been demonstrable before because no
  capture had ever produced a contradiction.
- **Status:** Accepted

### D-82 — The vector retriever had no point-in-time guard at all
- **When:** 2026-09-08T00:45:00-05:00
- **Decision:** `NewsIndex.search` takes the candidate's `as_of` and filters
  `a.date < @as_of` — strictly before — and `vector_retriever` passes `c.as_of`.
  The predicate is combined into the *same* `FILTER` as the symbol test, not a
  second clause.
- **Why:** PHASE-7 replaced the Alpaca news call with the vector index and
  carried over no date bound. `_SEARCH_AQL` filtered on symbol intersection and
  nothing else, so a candidate assessed as of 2026-05-11 retrieved an article
  published **2026-06-04** — 24 days of future information. Every other retrieval
  path in this project is point-in-time by construction: the traversal's
  `ALL`-quantified per-path `filing_date` guard, and `news_comention` being kept
  a view precisely so a caller cannot forget to bound it (D-16). The vector half
  was the one hole, and the showcase page states "the traversal cannot see a
  filing that had not happened" two sections above the leaked row.
  The single-`FILTER` form is not stylistic: ArangoDB's optimiser refuses
  `APPROX_NEAR_COSINE` when two separate `FILTER` statements sit between it and
  the `SORT`/`LIMIT` (`ERR 1554: Vector search could not be applied`), so the
  alternative — a second filter line — does not run at all. Established against
  the live instance by the `red-asof` agent before green began.
- **Outcome:** Closed and verified in data, not just in tests. `FILTER
  LENGTH(INTERSECTION(a.symbols, @symbols)) > 0 AND a.date < @as_of`; suite 89
  passed / 0 skipped with the live instance, `check_baseline.py --synthetic`
  unchanged. A fresh capture at as-of 2026-05-11 retrieves 0 of 18 articles on or
  after that date, latest 2026-04-21 — previously 2026-06-04.
- **Status:** Accepted

  **How it was found, which matters more than the fix.** Not by the test suite —
  80 tests passed over it. Not by me; I wrote and reviewed the wiring. It was
  found by an outside reviewer reading the *retrieved data* on a published page
  and noticing a date. Every guard in this project is enforced by construction
  except this one, which was enforced by a docstring: `vector_retriever` has
  claimed "only articles published strictly before the candidate's date are
  retrieved" since PHASE-5, and that sentence was aspiration for two phases.

### D-83 — The news query was near-contentless, and `direction` made it worse
- **When:** 2026-09-08T00:55:00-05:00
- **Decision:** `vector_retriever` asks
  `f"{symbol} catalyst: earnings, demand, guidance, production, regulation"`.
  The candidate's **direction is deliberately excluded** from the retrieval
  query, reversing the specification this project set two phases ago.
- **Why:** `f"news relevant to a {direction} move in {symbol}"` is almost
  contentless, and the nearest neighbours of a contentless query are contentless
  headlines: 18 of 18 retrieved articles across three candidates were the same
  `"Market-Moving News for <date>"` template. Every assessment this pipeline has
  ever made used market-wrap noise as its news context, so this is a production
  defect, not a presentation one. Probed against the live index to locate the
  fault: the corpus and the embeddings are fine — `"iPhone production cuts,
  component orders and supplier demand"` returns Ming-Chi Kuo on iPhone Air demand
  at 0.713 against the same filter and `as_of`.
  **Direction was measured, not assumed, and it loses.** With it, AVGO returns
  "Smart Money Is Betting Big In AVGO Options" three times and TSLA returns
  "Trade Strategy For SPY, QQQ, AAPL..." three times — speculative trading
  chatter, the same pathology reintroduced. Without it, all three candidates get
  on-company substance (*Apple Earnings Are Imminent*, *Broadcom Slides 4%
  Despite Q4 Beat*, *Tesla's Q4 Earnings Looms*). Direction belongs to the thesis
  being assessed, not to what context to retrieve: news is not written
  directionally, so asking for it retrieves people speculating about direction.
  **The score falls and that is correct.** `catalyst` scores 0.516 where the old
  query scored 0.567. Cosine measures proximity to the query, not usefulness — a
  vague query sits near the corpus centroid and therefore scores well against
  almost everything, so the *high* number was the symptom. Optimising the visible
  score would have selected the worse retriever; the alternative form (`drivers`)
  scored highest at 0.700 and returned generic stock chatter.
  The query is also uneven across candidates — thinner for AVGO than for AAPL or
  TSLA — and that is kept rather than tuned away. AVGO genuinely has less
  retail-facing coverage, and tuning a template until it flatters three chosen
  seeds is the cherry-picking this project has spent its whole life avoiding.
- **Outcome:** pending
- **Status:** Accepted — supersedes the query specification in D-77's PHASE-7

### D-84 — "Already responded" is a separate axis from "contradicted", and the ratio is signed
- **When:** 2026-09-08T18:22:00-05:00
- **Decision:** A scan-discovered candidate `X` now carries two new fields
  alongside its verdict: `origin_status` (`"open"` | `"responded"` |
  `"opposed"` | `None`) and `room` (`float | None`). `fuse_evidence` classifies
  on two **signed** quantities computed the same way for both symbols —
  `y_component = z_Y * want`, `x_component = z_X * want`, where `Y` is the
  candidate's `origin_leader` and `want` is `+1`/`-1` for an up/down thesis:
  `y_component <= 0` skips entirely (no evidence, both fields `None`);
  `x_component < 0` is **opposed** (`room=None`, one contradicting
  `Evidence(kind="lag_response", weight=1.0)`); `x_component >= y_component` is
  **responded** (`room=0.0`, and **no `Evidence` at all**); otherwise **open**
  (`room = 1 - x_component/y_component`, one corroborating unit). The tie
  `x_component == y_component` lands in *responded*, not open-with-zero-room.
- **Why:** the alternative that lost was an unsigned single formula,
  `responded = |z_X| / |z_Y|`. It is shorter and needs no sign handling, but it
  is sign-blind: a candidate that moved 1σ *against* the thesis and one that
  moved 1σ *with* it but has not caught up both yield `0.5`, so a room-sorted
  list would rank a refuted thesis in the middle of the "still has room" names
  instead of at the bottom. That collapses the exact distinction this whole
  change exists to keep. Emitting no `Evidence` for *responded* (rather than a
  contradicting one) is the same distinction on the verdict side: the
  opportunity is spent, not refuted, so it must not be able to push `verdict`
  to `"contradicted"` on its own. Reusing the existing three-way `verdict`
  string for this also lost — `"neutral"` already means "insufficient
  evidence, or a tie", and overloading it would make one string mean two
  unrelated things.
  `room` is deliberately built from z-scores only and multiplies by **no**
  transfer coefficient, measured or assumed: D-74's pooled slope for exactly
  this customer→supplier relationship is `b=-0.0015` against a pre-registered
  `0.02` threshold — a null on the wrong side of zero — so any assumed fraction
  of `Y`'s move appearing in `X` would present as fact the one quantity this
  project measured and did not find. It orders candidates against each other
  and is never shown as an expected return, target, or bp/% figure. This also
  keeps the change clear of `LagEdge.beta` entirely (Q-39).
- **Outcome:** **Built, and it does change verdicts — but not in the way the
  design expected.** Exercised on the real 2026-05-11 scan (3,204 symbols swept,
  255 movers, 19 candidates): the new input changed the verdict for **11 of 19**,
  because those 11 had *no* correlated-neighbour evidence at all and were
  previously unassessable — that is D-85's gate change doing the work, not the
  classification. Where both inputs existed, all **8 of 8** agreed, which is
  weak reassurance since both read the candidate's own move (Q-40).
  Two findings against the design:
  **(1) the `responded` bucket was empty — 0 of 19.** `x_component >= y_component`
  means "the candidate moved at least as far as a company that just moved ≥2σ",
  which almost nothing clears in a 3-session window. The state the user actually
  asked for ("we don't care much about candidates that moved too much already")
  therefore never fires. Logged as Q-41.
  **(2) `origin_status` is perfectly collinear with `verdict` on this data** —
  8 `open` → all `corroborated`, 11 `opposed` → all `contradicted`, exactly. The
  label carries no information the verdict does not. The only genuinely new
  quantity is `room`'s *magnitude*, which does spread (0.9859 … 0.3896, median
  0.7453) and does order the open names against each other.
  The falsifiability check the plan cared about still holds in the code
  (`test_lag_response_already_responded_does_not_read_as_contradicted`); it just
  has no real-data instance yet to exercise it.
- **Status:** **Superseded by D-87.** Measured over 432 scan dates and
  replicated independently: `room`'s denominator is unfounded and the ordering
  does not predict. The "empty bucket" reasoning in this entry's Outcome was
  also wrong — see Q-41.

### D-85 — `fuse_evidence`'s `if not leaders: continue` gate now admits the origin-leader path
- **When:** 2026-09-08T18:22:00-05:00
- **Decision:** the guard becomes
  `if not leaders and not (c.origin_leader and c.origin_leader in shocks): continue`,
  with `shocks`/`movers` built above it rather than below. A candidate with
  `origin_leader=None` and no correlation neighbours still skips, byte-identically
  to before.
- **Why:** found by reading the code during execution, not written into
  PLAN-2026-09-08-unresponded-lag, which is why it is logged separately. The old
  gate ran before `shocks` was even built, so a scan candidate with no
  correlation neighbours never reached fusion, got no `_by_key` entry, and was
  dropped by `assess()` under D-27 — meaning a `lag_response` unit could never
  be the *only* evidence for a candidate. That would have made the "responded
  alone cannot manufacture a verdict" and "lag_response weight alone clears
  `MIN_EFFECTIVE`" tests unwritable, because there would be no `Assessment` to
  assert on at all. Leaving the gate and weakening those two tests was the
  alternative; it would have removed the only checks that pin `lag_response`'s
  independent behaviour.
- **Outcome:** **Correct, but it never fires on real data — and I mis-reported
  why it mattered.** I claimed this gate change made 11 of 19 candidates
  assessable that D-27 had been dropping. That was wrong: I conflated `leaders`
  with `movers`. Measured on the real 2026-05-11 scan, **every one of the 19
  candidates has exactly 20 `leaders`** (`topk=20` against a 3,204-symbol
  universe), so `not leaders` is never true and the old gate never dropped any
  of them. What was 11-of-19 is `movers == 0` — the neighbours existed and none
  of them moved. The gate change is still correct in principle (a candidate
  whose only route in is `origin_leader` should reach fusion) and harmless, but
  it changed nothing observable, and the "11 of 19" figure attached to it in
  D-87 and in commit `b4fa3d7`'s message is wrong for the same reason.
  The only way `leaders` is empty is a candidate absent from the price file or
  short of history, and `route_on_neighbourhood` sends that to `END` before
  fusion is reached.
- **Status:** Accepted — but see the corrected Outcome; the justification
  originally given for it was measured wrong

### D-86 — A missing candidate move is an explicit no-result, not `room = 1.0`
- **When:** 2026-09-08T19:05:00-05:00
- **Decision:** `fuse_evidence` skips the D-84 lag-response classification
  entirely when `c.symbol` has no `Shock` — no `Evidence`, no `room_by_key`
  entry, no `origin_status_by_key` entry — and appends a message to a new
  `"errors"` list it now returns, in `graph_retriever`'s existing house style
  (`f"{c.symbol} {c.as_of}: no shock for candidate's own move"`).
- **Why:** `cand_z = shocks[c.symbol].sigma if c.symbol in shocks else 0.0`
  fed `x_component`, so an absent candidate shock read as `x_component == 0`,
  which D-84 classifies as **open with `room = 1.0`** — the maximum. Missing
  data about the candidate's own move therefore masqueraded as the strongest
  possible signal and sorted to the *top* of the ranked list, with nothing to
  indicate anything was wrong. The alternative was leaving it: verified latent
  (0 of 19 on the real 2026-05-11 scan, no `NaN` rooms), so nothing published
  is affected. Rejected because the failure mode is silent and inverted —
  the worst kind — and CLAUDE.md requires a silent path be given an explicit
  outcome rather than a plausible-looking default.
  Deliberately **not** widened to the pre-existing `leader_move` loop, which
  keeps its own `else 0.0` fallback: that branch feeds published
  corroboration-mode results, and changing it would move them. Scoped to the
  lag-response block only.
- **Outcome:** Verified. The real 2026-05-11 scan is byte-identical after the
  change — same 19 candidates, same 8 open / 11 opposed, same rooms
  (0.9859 … 0.3896) — with `errors` empty, confirming the guard costs nothing
  when the data is present. 118 passed, 9 skipped; `check_baseline.py
  --synthetic` unchanged at 6 identical rows.
- **Status:** Accepted

### D-87 — `room` and `origin_status` are deleted: the denominator is unfounded and the ordering does not predict
- **When:** 2026-09-09T03:50:00-05:00
- **Decision:** Remove `room`, `origin_status`, `rank_by_room`, and the
  `opposed` `lag_response` `Evidence` unit. Replace with **description only** —
  a plain sentence naming the candidate's own thesis-signed move in its own
  sigma units and the leader that surfaced it. No threshold, no bucket, no
  denominator, no ordering, no implied magnitude. `Candidate.origin_leader`
  (D-84's PHASE-1) is kept; the description needs it.
- **Why:** two independent reconstructions agree — a quant consult over 432
  scan dates / 13,063 supplier-events, and a separate rebuild over 30 dates /
  209 linked events from a different data file and different code.
  **(1) The denominator assumes what D-84 claimed it avoided.** D-84 states
  `room` "multiplies by no transfer coefficient, measured or assumed." That is
  incorrect: `1 - x/y` divides one z by another, which *is* the assumption that
  the candidate should move as many of its own sigmas as the leader moved of
  its — a standardised pass-through of exactly 1.0. Measured, that coefficient
  is +0.18 contemporaneously and statistically zero forward, and its
  interaction with the leader's shock size is null (−0.050 (0.223), z=−0.22;
  −0.031 (0.101), z=−0.31 on the replication). The quantity `room` treats as
  proportional to `y_component` is not proportional to it at all, so the
  division is arithmetic, not economics.
  **(2) The ordering does not predict.** Thesis-signed forward return regressed
  on the ratio: −0.036 (0.112), z=−0.32; replication +0.022 (0.275), z=+0.08.
  Flat, both signs, neither significant.
  **(3) The buckets are interchangeable.** Linked-minus-control forward premium
  is *identical* for `open` and `opposed` in both samples (+0.084/+0.084;
  −0.206/−0.206) — and the two samples disagree on the sign, which is itself the
  finding: the level is sampling noise.
  The alternative that lost was keeping `room` "honestly labelled" with the
  nulls stated on the page. Rejected because a sort key presented at all is a
  claim about which names deserve attention, and this null is now *measured*,
  not merely unmeasured — the distinction this project has drawn everywhere
  else. Deleting only the `responded` bucket also lost: it would have left the
  unfounded denominator in place.
  Scope note, logged as two different claims: `room`'s magnitude is
  **unfounded** (assumes a contradicted coefficient); the open/opposed sign
  test is **sound in construction but non-predictive in fact** — it never
  divides, so it assumes no pass-through; it is deleted on (3), not on (1).
- **Outcome:** Done, in `b4fa3d7`. `room`, `origin_status`, `rank_by_room` and
  the `lag_response` `Evidence` unit are gone (`grep -rn 'lag_response' src/`
  returns nothing); each scan card now carries one inert sentence, e.g.
  "COHR has moved +0.03σ toward the thesis; surfaced by DELL." Verified on a
  live scan: descriptions render, no card prints `null`, and the pre-existing
  verdict pills still colour correctly. 111 passed / 9 skipped (from 118),
  `check_baseline.py --synthetic` unchanged at 6 identical rows at every phase
  boundary. The falsifiable pin that `description` never votes is
  `test_description_flows_through_to_assessment_without_affecting_verdict`'s
  exact `effective_evidence == 1.0`.
  Two side effects worth recording: Q-40 became moot (nothing left to collide
  with), which in turn let the replacement tests call `retrieve_neighbourhood`
  for real — closing the old suite's caveat that its "end-to-end" only held
  from `leader_state` onward. And the D-85 admit gate was kept in simplified
  form (`if not leaders and not c.origin_leader`). **Correction:** the
  "11-of-19 candidates it made visible" claim repeated here is wrong — see
  D-85's corrected Outcome. Those 11 had 20 neighbours each and were always
  being assessed; what they lacked was any neighbour that *moved*. The gate
  change is right in principle and observably inert. D-27 governs evidence
  *sourcing*, not display, and a description cannot manufacture confluence
  because it is not evidence.
- **Status:** Accepted — supersedes D-84

### D-88 — The supply graph's contemporaneous effect is a selection artefact, not a supply-chain effect
- **When:** 2026-09-09T03:50:00-05:00
- **Decision:** Record that the linked-vs-control co-movement this project has
  been reading as a supply-chain effect is explained by trailing correlation,
  and **do not act on it in this change** — the page's claims about the graph
  need revisiting on their own terms, not as a side effect of removing `room`.
- **Why:** suppliers of a shocked leader are, first and foremost, names with
  much higher trailing correlation to that leader. Date-paired linked minus
  matched control, netting out each pair's own trailing 60-day correlation
  (`resid = x_component − ρ·y_component`):

  | | consult (432 dates) | replication (30 dates) |
  |---|---|---|
  | trailing ρ with leader | +0.176 (0.007), z=+24.85 | +0.245 (0.025), z=+9.98 |
  | `x_component` | +0.360 (0.035), z=+10.17 | +0.363 (0.112), z=+3.23 |
  | residual `x − ρ·y` | −0.093 (0.031), z=−2.98 | −0.317 (0.125), z=−2.54 |

  The raw co-move agrees to three decimals across two independent
  reconstructions, and once ρ is removed the supply-specific excess is not
  merely absent but **negative and significant in both** — linked names respond
  *less* than their own trailing correlation predicts. At this horizon the
  supply graph is a correlation filter with extra steps, and the correlation
  path was already nulled (README: −4.6 bp, z=−0.72; D-59/D-60 found the lag-0
  edge tautological). This does not automatically condemn the scan — the graph
  supplies *direction* and an interpretable rationale, and the forward
  membership premium (+0.084σ, z≈1.6, not established) is not derived from ρ —
  but the contemporaneous number can no longer be cited as evidence the graph
  adds anything over a correlation screen. Logged rather than acted on because
  it bears on the whole GraphRAG premise and deserves its own measurement, the
  way Q-38 did.
- **Outcome:** pending — see Q-42
- **Status:** Accepted

### D-89 — A join node whose in-edges land in different supersteps fires twice; the news node stays where it is
- **When:** 2026-09-09T05:05:00-05:00
- **Decision:** `vector_retriever` keeps fanning out from `route_on_neighbourhood`,
  in the same `Send` wave as `leader_state`. The proposal to dispatch it from
  `START` — so news retrieval runs concurrently with `graph_retriever` rather
  than after it — is **rejected as unsafe**, not deferred.
- **Why:** the proposal assumed LangGraph runs a join node once regardless of
  which superstep each input arrives in. That is false on the installed
  version. Reproduced twice independently, with a minimal graph mirroring this
  one's exact shape (`START -Send-> {A,V}`; `A -Send-> L`; `L,V -> J`):

      J fired 2 time(s):
        a_out=['a:X']         v_out=['v:X']    <- partial, L had not run
        a_out=['a:X','l:X']   v_out=['v:X']    <- full

  `context_fusion` runs exactly once today *because* both its sources are Sent
  by the single `route_on_neighbourhood` call and therefore complete in one
  superstep. Moving `vector_retriever` to `START` would put it a full superstep
  ahead of `leader_state` (which genuinely depends on `graph_retriever`), so
  `context_fusion` would fire once on partial state — `leader_shocks` not yet
  written, every `movers` list empty, and a spurious "no shock for candidate's
  own move" error appended for `origin_leader` candidates whose shock simply
  had not arrived — and again on complete state. Because `evidence` and
  `errors` use `Annotated[..., add]`, the two passes are **concatenated, not
  replaced**. `evidence_by_key` would self-heal (dict `_merge`, last write
  wins); the flat channels several tests read directly would not.
  Two alternatives lost: reopening `defer=True` (declined by D-35, and this is
  not a strong enough reason to reverse it), and making `fuse_evidence`
  idempotent (rewrites the well-tested Q-12 cluster-discount logic to buy
  diagram accuracy — disproportionate).
  A third, narrower change was offered and also declined: ungating
  `vector_retriever` from `lag_edges` so dropped candidates still get news.
  It is safe, but it does **not** move the node in the diagram — which was the
  entire motivation — and it buys an invisible gap (no surface displays news
  for an unassessed candidate) at the cost of a vector query per dropped
  candidate plus a phantom `news_by_key` entry. Declined as work that does not
  serve its own stated goal.
  What remains true and is worth stating plainly: `vector_retriever` has **no
  data dependency** on the graph half (D-83 reduced its query to `c.symbol`
  alone), so its position is a scheduling artefact, not a requirement. The
  pipeline is ordered the way it is because of how the join is built, not
  because news needs the graph. That is now a presentation problem, handled by
  rewriting the page's narrative rather than the topology.
- **Outcome:** pending — no code changed; `src/lagmatrix/graph/nodes/vector_retriever.py`'s
  docstring was corrected in `829d279` to stop claiming the node reads the
  leader list.
- **Status:** Accepted

### D-90 — The pipeline has no lag in it, and the premise it is named for has been tested twice and failed
- **When:** 2026-09-09T05:40:00-05:00
- **Decision:** State plainly, in the log and on the page, that the shipped
  pipeline is a **contemporaneous co-movement detector with a supply-chain
  overlay** — not a lead-lag engine — and build the symbol-in mode the project's
  stated purpose implies as a **descriptive** feature: relationships and
  history, never a prediction.
- **Why:** the user restated the goal as "given a leader, find followers it
  affected historically, and show how the leader might move them." Checked
  against the code, none of that is implemented as stated:
  `graph_retriever.py:61` creates every correlation edge with
  `lag_days=0,  # contemporaneous correlation; lag estimation is future work`,
  and `lag_days` on a supply edge is graph *hops*, not time. Nothing in
  `src/` computes a lagged relationship at all; the only shifted-series code is
  in one-off `scripts/` experiments.
  The premise itself is not merely unbuilt — it is measured null at both scales
  anyone has tried, each pre-registered before the data was seen:
  **D-59/D-60** (intraday, 1,174,267 minute bars, 83 candidate-dates, funds
  excluded): 89.2% of dates peak at **lag 0**, sign test 16 up / 25 down,
  **p = 1.000**; and **D-73/D-74** (daily, `supplier_excess(t+1) = a + b ·
  customer_excess(t)`, threshold |b| >= 0.02): pooled **b = −0.0015**, z = +0.43.
  D-88 then showed the contemporaneous supply co-move is explained by trailing
  correlation with a *negative* residual. D-60's own words: "Genuine non-fund
  peers move **with** the candidate at minute resolution, not before it, so the
  condition the pipeline looks for — neighbours moved while the candidate has
  not — barely occurs."
  The alternative that lost was building the propagation estimate anyway, in
  some softened form. Rejected for the same reason D-87 deleted `room`: it
  would assert the one quantity this project has now gone looking for three
  times and not found. What is left, and is genuinely worth building, is the
  half that needs no forecast — a symbol-in front door (absent today; there is
  no way to ask "given AAPL, show me its followers") and a per-pair track record
  drawn from history, with its `n` stated, whose most likely honest reading is
  "nothing consistent here".
- **Outcome:** pending — planned in `docs/plans/PLAN-2026-09-09-leader-in.md`.
- **Status:** Accepted

### D-91 — Expose how many neighbours were checked, so a negative result reads as one
- **When:** 2026-09-09T05:50:00-05:00
- **Decision:** `fuse_evidence` returns `neighbours_by_key: dict[str, int]` —
  `len(leaders)`, the count of price-correlated neighbours considered — threaded
  onto `Assessment.neighbours: int`. The card can then say "20 related companies
  checked, none moved unusually" instead of a bare
  `0 supporting · 0 contradicting · effective evidence 0`.
- **Why:** the user's objection was that a reader cannot tell "we looked and
  found nothing" from "we didn't look". Investigating that produced a
  correction to my own earlier claim (see D-85's Outcome): the second state
  essentially does not occur — `topk=20` against a 3,204-symbol universe gives
  every candidate 20 neighbours, and all 19 on the 2026-05-11 scan had exactly
  20. So the real problem is narrower and simpler than stated: the *common*
  outcome, "checked twenty, none moved", is currently indistinguishable from
  nothing having happened at all. Exposing the count makes a null result legible
  as a null result rather than as an absence.
  `len(movers)` is deliberately **not** exposed: every mover produces exactly
  one `Evidence`, so `len(movers) == n_supporting + n_contradicting`, which the
  card already shows. One new number, not two.
  The alternative that lost was rewording `rationale`, which already exists and
  is already displayed. Rejected because `rationale` is prose assembled for the
  contradicted case and is not machine-readable; a caller wanting the count
  would have to parse English out of it.
  **The invariant, and the reason it is stated here rather than assumed:** this
  count carries no weight, never enters `effective_evidence`, and is never
  rendered as a quality or confidence score. D-87 deleted `room` for being a
  presented number that implied a claim the measurements did not support; a
  neighbour count is a fact about what the pipeline did, and must stay that.
  Pinned by a test asserting `effective_evidence` is unchanged across two runs
  whose neighbour counts differ.
- **Outcome:** Done. The count reaches the card, which was the whole point and
  was missing until now — the field shipped in `889a3f2` but was never put in the
  SSE payload, so it existed and nobody could see it. A scan card now reads
  `0 supporting · 0 contradicting · effective evidence 0 · 20 related companies
  checked, none moved unusually` instead of trailing off after the zero. The
  invariant holds: `test_neighbours_count_never_affects_effective_evidence`
  still requires 1.0 with three leaders and with one.
- **Status:** Accepted

### D-92 — Pre-registration: does a shock propagate along the supply chain with a hop-dependent delay?
- **When:** 2026-09-09T06:05:00-05:00
- **Decision:** Run one test, specified in full **before any result is seen**,
  of the project's central and still-untested claim. D-73/D-74 tested exactly
  one cell of the relevant grid — hop 1, t+1, pooled, `b = −0.0015`. This tests
  the *shape* across hops and horizons instead.

  **Population.** The 818 `supplies_to` edges: 76 customers, 105 suppliers.
  Hop 1 = a direct supplier of the leader; hop 2 = a supplier of one of those.
  A (leader, follower, episode) triple is admitted only when the linking
  filing's `filing_date <= ` the episode date (D-16 point-in-time, the same
  guard `laggers_of` applies). Measured before writing this: **741 hop-1 pairs,
  14,416 triples**, 67 leaders with ≥2σ episodes, median 18 episodes per pair
  over 2016-09-06..2026-09-04. Hop-2 counts are not yet measured and are part
  of the run.

  **Episode.** A leader's non-overlapping 3-session window with `|z| >= 2.0`
  on `shocks.standardised_moves` against a 60-session baseline — the same
  definition `MarketScan.shocked_leaders` already uses, so the test measures the
  thing the product actually keys on.

  **Response.** For each follower, thesis-signed market-excess return over
  `h ∈ {1, 2, 3, 5, 10}` sessions after the episode window, normalised by that
  follower's own trailing volatility, market-excess against the equal-weighted
  universe — matching D-73/D-74's construction so results are comparable.

  **Primary test, one only.** Pooled OLS

      excess(F, h) = a + b1·hop2 + b2·late + b3·(hop2 × late)

  where `late = 1` for `h ∈ {5, 10}` and `0` for `h ∈ {1, 2}`. The propagation
  story predicts **b3 > 0**: a hop-2 follower's response is relatively more
  concentrated at longer horizons than a hop-1 follower's. This is a single
  prediction about *ordering*, deliberately chosen over testing 20 grid cells
  separately, where roughly one would clear α=.05 by chance.

  **Economic threshold, declared now: b3 >= 0.25σ.** Chosen for cost, not for
  significance: below roughly 0.25 of a follower's own sigma (~0.5%) a timing
  differential cannot survive a round trip, whatever its p-value.

  **Errors clustered by date** — every chain shares one market shock on a given
  session. D-71's lesson, applied in advance rather than discovered afterwards.

  **Mandatory secondary, from D-71:** the same interaction estimated per year,
  with Cochran's Q and I². A pooled estimate resting on one regime is reported
  as such.

  **Decision rule, pre-committed.** Claim hop-dependent propagation only if
  `b3 >= 0.25` **and** `z >= 2` with date-clustered errors **and** I² < 75%.
  Any other outcome — including a large but heterogeneous estimate, or the right
  sign below threshold — is reported as a null and closes the question.

  **Power, stated before running.** D-88 established the binding constraint is
  chains, not dates: 105 suppliers co-move, so effective independent information
  is far below 14,416. The run reports its own realised MDE alongside the
  estimate; if MDE exceeds the 0.25 threshold the test could not have detected
  the effect it was looking for and is reported as **underpowered, not null** —
  the distinction D-73 got wrong and D-74 existed to fix.
- **Why:** the alternative was looking at the grid first and pre-registering
  only if it looked promising. Rejected: D-59's own record notes that silently
  amending a design after seeing the result "would be choosing the analysis
  after seeing the result", and this project's credibility rests on not having
  done that. Registering costs one commit.
- **Outcome:** **Run, and the honest verdict is UNDERPOWERED, not null — the
  graph is one hop deep.** `scripts/experiment_hops_days.py`, 14,115 triples,
  67 leaders, 98 followers, 461 dates.

      hop-1 pairs 117      hop-2 pairs 6
      n per cell: hop 1 -> 2,797     hop 2 -> 26

      PRIMARY (date-clustered)
        hop2 x late  +0.1232 (0.1387)  z = +0.89
        realised MDE = 0.3884 sigma    threshold = 0.25   -> UNDERPOWERED
      SECONDARY  4 yearly estimates, Q = 0.8, I^2 = 0%
                 per-year b3: +0.34  -0.24  +0.07  +0.24

  `b3` carries the **predicted sign** but the realised MDE (0.39) exceeds the
  pre-declared threshold (0.25), so by D-92's own rule this cannot be reported
  as a null: the test could not have detected the effect it was looking for.
  Recording it as a null would repeat exactly the error D-73 made and D-74 was
  written to correct.

  **The cause is structural and is the finding worth keeping.** A hop-2 path
  needs a supplier who is themselves a customer with suppliers. Measured:
  **4 of 105 suppliers qualify (4%)** — BKR, GEV, HWM, QRVO — which is why only
  6 hop-2 pairs exist. The graph is 76 stars of median 3 suppliers, not a
  network, because only the *customers'* 10-K disclosures were ever ingested and
  their suppliers were left as leaves. The hops dimension the product's whole
  premise rests on is not testable against this graph at any sample size, and no
  amount of additional price history changes that (D-88's "chains, not dates",
  now with a specific cause).

  **What it would take**, stated concretely so it is actionable: ingest the
  customer-concentration disclosures of the 105 existing suppliers, turning
  leaves into interior nodes. That is the same `edgar/relations.py` pipeline
  already built and audited (D-78), pointed at a different set of filers — a
  data-acquisition task with a known method, not a modelling problem.
- **Status:** Accepted — result is *underpowered*, question remains open; see Q-45

### D-93 — Pre-registration: does ANY lagged pairwise structure survive out-of-sample, across the whole history?
- **When:** 2026-09-09T06:30:00-05:00
- **Decision:** Test the project's premise at full scale and in its most general
  form, specified **before any result is seen**. Not news-anchored, not
  supply-graph-restricted: every pair of symbols, every lag, ten years.

  **Data.** `data/bars-10y.parquet` — 2,183 symbols, 2,514 sessions
  (2016-09-06..2026-09-04). Returns are **market-excess** (each session's
  equal-weighted universe mean subtracted) before anything else. Without this
  every pair correlates through market beta and the exercise measures the index.

  **Split, by time, no shuffling.** Discovery = the first 60% of sessions.
  Validation = the last 40%. A pair selected in discovery is tested only on
  validation. This is the entire defence against 4.8M pairs per lag: at α=.05,
  roughly 240,000 pairs would clear on noise alone, so in-sample significance
  is worthless and is not used as a criterion anywhere.

  **Lags.** `k ∈ {1, 2, 3, 5, 10}` sessions. For each k, the full pairwise
  lagged correlation `corr(X_t, Y_{t+k})` over the discovery window.

  **Selection.** The top 1,000 pairs by `|lagged corr|` in discovery, per lag.
  Self-pairs excluded. Both directions of a pair are distinct (X leads Y is not
  Y leads X).

  **Primary test, one only.** Of those selected pairs, what fraction show the
  **same sign** of lagged correlation in the validation window? Under the null
  that discovery found only noise, this is 50%. Binomial test against 0.5.

  **Economic threshold, declared now: sign-agreement >= 60%, AND mean
  |lagged corr| in validation >= 0.03.** The second condition matters
  independently: a 58% sign rate on correlations of 0.004 is statistically
  detectable at n=1,000 and economically nothing. Both must hold.

  **Decision rule, pre-committed.** Claim exploitable lagged structure only if
  a lag `k` shows sign-agreement >= 60% **and** validation mean
  `|corr| >= 0.03` **and** binomial p < 0.01. Anything else — including a
  significant sign rate on trivial magnitudes — is a null for that lag, and a
  null at every lag closes the question.

  **Mandatory secondary.** Report the same statistics for a **shuffled control**:
  the identical procedure with validation-window dates randomly permuted, which
  destroys any real lead-lag while preserving each series' own distribution. If
  the control shows a comparable sign-agreement rate, the primary result is an
  artefact of the procedure rather than of the market, and is reported as such
  regardless of what the primary number was.

  **Power.** n=1,000 selected pairs per lag gives a standard error on the sign
  rate of ~1.6pp, so 60% vs 50% is detectable with wide margin. Unlike D-92,
  this test is not sample-limited — if it returns a null, that null is real.
- **Why:** the alternative was scanning for pairs and reporting the best ones,
  which is what almost every version of this idea does and is exactly how
  240,000 false positives get published as a signal. Out-of-sample validation
  costs one extra window and makes the answer trustworthy either way. The
  shuffled control was added because a time-split alone does not protect against
  a procedural artefact (e.g. persistent volatility clustering producing sign
  agreement with no lead-lag content at all).
  Also rejected: restricting to the supply graph or to news-anchored events.
  Both are strict subsets of this test, both are coverage-limited (76 customers;
  20 months of articles), and both have already returned nulls or underpowered
  results (D-74, D-88, D-92). If general lagged structure does not exist, no
  subset of it will.
- **Outcome:** **NULL at every lag, and this one is properly powered.**
  `scripts/experiment_lag_matrix.py`. 1,573 symbols cleared 90% coverage in both
  windows — **2,472,756 directed pairs per lag**. Discovery 2016-09-07..
  2022-08-31 (1,507 sessions), validation 2022-09-01..2026-09-04 (1,006).

      lag  sel |c| disc  val mean |c|  sign agree      p    shuffled control
        1       0.2059        0.0281      52.9%   3.6e-02        47.0%
        2       0.1776        0.0248      48.2%   8.8e-01        49.6%
        3       0.1820        0.0247      47.6%   9.4e-01        51.8%
        5       0.2456        0.0261      50.4%   4.1e-01        74.9%
       10       0.1663        0.0280      38.7%   1.0e+00        42.0%

  No lag met any of the three pre-committed conditions. Sign agreement never
  reached 60% (best 52.9%); validation `|corr|` never reached 0.03 (best 0.028,
  against in-sample selections of 0.17-0.25); and the **shuffled control matched
  or beat the real result at four of five lags** — at lag 5, 74.9% against 50.4%
  — so what agreement exists is generated by the procedure, not by the market.
  **This is a real null, not an underpowered one.** SE on the sign rate is
  ~1.6pp at n=1,000; a genuine 60% effect could not have been missed. That
  distinction is why D-93 was designed with a fixed n rather than whatever the
  data happened to supply, after D-92 could not answer its own question.
  The selected pairs show the mechanism directly: `TPC → TRGP` +0.379 in
  discovery and +0.043 in validation; `BTU → PLUG` +0.359 → −0.022. `TPC` takes
  four of the top ten slots — one series with unusual variance manufacturing
  partners. Selecting the best of 2.47M pairs produces correlations of 0.38 that
  are worth 0.04 the moment they are asked to predict anything.
  **What this closes.** Supply-graph propagation (D-74, D-88, D-92) and
  news-anchored co-movement are strict subsets of this test. A subset cannot
  contain structure the superset does not have, so those lines of enquiry are
  closed too — not for want of coverage, which was the D-92 story, but because
  the thing they were looking for is not in the data at any lag. Q-45
  (deepening the graph) would still improve coverage, but it can no longer be
  justified as a route to finding propagation.
  **What survives.** Everything descriptive: the supply edges and their filing
  sentences, the news retrieval, contemporaneous co-movement, and the honest
  reporting of all of it. What does not survive is any claim that a leader's
  move anticipates a follower's.
- **Status:** Accepted — the premise is answered, negatively and with power

### D-94 — Two-hop chains beat a coin flip on direction and carry no magnitude; the hops idea closes
- **When:** 2026-09-09T06:55:00-05:00
- **Decision:** Close the multi-hop propagation line. Chains are not a route to
  a prediction, and the product will not claim one.
- **Why:** D-93 tested every *direct* pair and found nothing that replicates,
  which already implies most of this — a chain is built from links, and a
  two-hop path X→Y→Z with delays k1, k2 implies a direct X→Z relationship at
  lag k1+k2, itself one of the 2.47M pairs D-93 nulled. But that argument does
  not exclude a *conditional* effect: X predicting Z only when Y also moved, in
  a way that cancels out of the marginal. That is a genuinely different
  hypothesis, so it was tested rather than argued away
  (`scripts/experiment_chains.py`, same windows, same market-excess
  construction, same out-of-sample discipline).
  890 chains formed from the 300 strongest links per leg, X-(1)->Y-(2)->Z,
  evaluated as X→Z at lag 3:

      chained X->Z             disc |c| 0.1113   val |c| 0.0250   sign 57.6%
      random pairs, same lag   disc |c| 0.0265   val |c| 0.0248   sign 52.9%
      binomial p vs 50%: 2.9e-06        lift over random: +4.7pp

  **The sign effect is real and the magnitude effect is absent.** Chained pairs
  agree on direction 57.6% of the time out of sample, p = 2.9e-06 — not noise.
  But their validation `|corr|` is 0.0250 against random pairs' 0.0248: the same
  number. Knowing the direction 57.6% of the time on an expected move of 0.025σ
  (~0.05%) is not tradeable and is not a basis for any claim on the page.
  Note also that random pairs score 52.9%, not 50% — the same procedural bias
  D-93's shuffled control exposed at four of five lags. Measured against that
  honest baseline the chain lift is **+4.7pp, not +7.6pp**, which is the number
  that would have been reported by anyone comparing to a theoretical 50%.
  Recorded as a null by the pre-committed standard (sign >= 60%, p < 0.01,
  lift > 5pp): it fails the sign threshold and the lift threshold, and has no
  magnitude at all.
- **Outcome:** Verified. Together with D-93 this closes the premise: no direct
  lagged structure, and no conditional structure via chains beyond a
  sign-only effect with no size. What remains defensible is descriptive —
  relationships, filings, news, contemporaneous co-movement — none of it
  predictive.
- **Status:** Accepted

### D-95 — Contemporaneous co-movement replicates out of sample; the graph should be built from it
- **When:** 2026-09-09T07:15:00-05:00
- **Decision:** Build the graph from **measured co-movement over price history**,
  with a calibrated out-of-sample confidence interval on every edge, rather than
  from 10-K disclosures alone. The confidence number describes **how reliably two
  names move together**, never what one does after the other.
- **Why:** every lagged test failed (D-74, D-88, D-92, D-93, D-94). Nobody had
  tested **lag 0** out of sample, and D-60 had already hinted it was the live
  quantity — genuine peers at median rho 0.505 against 0.226 for controls,
  peaking at lag 0 on 89.2% of dates. Run through D-93's own machinery, same
  windows (discover 2016-09..2022-08, validate 2022-09..2026-09), 1,573 symbols:

      lag 0, top 1000 pairs   disc |c| 0.8268  val |c| 0.7834  retained 94.8%
      lag 1 (D-93)            disc |c| 0.2059  val |c| 0.0281  retained 14%

  And across **1,236,371 pairs**, with same-company artefacts screened
  (`|disc corr| >= 0.95` removed — 7 pairs: GOOGL/GOOG, Z/ZG, FOX/FOXA,
  NWS/NWSA, plus NATL/LINE at exactly +1.000 flipping to −0.325, which is a data
  artefact, not a market fact), `corr(discovery, validation) = 0.640`:

      disc band        n        val mean   val sd   sign holds
      0.3-0.40   115,155           0.260    0.121        98.7%
      0.4-0.50    39,990           0.350    0.139        98.1%
      0.5-0.60    12,291           0.460    0.164        96.2%
      0.6-0.70     4,707           0.587    0.167        98.1%
      0.7-0.95     2,214           0.712    0.129        99.5%

  A pair measured at 0.6-0.7 lands at 0.587 ± 0.167 four years later with its
  sign intact 98% of the time. The shrinkage is mild and consistent, which is
  what makes an interval honest rather than decorative.
  **Two things stated so they are not mistaken later.** (1) D-93's shuffled
  control is **invalid at lag 0** — permuting dates permutes both series
  together and preserves contemporaneous correlation exactly. It scored an
  identical 99.6% and tests nothing here; the out-of-sample split is the real
  control. (2) The confidence is about the *relationship*, not a forecast.
  Same-day co-movement replicates at 0.640; next-day prediction replicates at
  0.028. The product may state the first and must never imply the second.
  Coverage, which blocked everything else: **1,573 symbols** with measurable
  relationships against **76 customers** in the filings graph — about 20x — and
  it needs no new data collection, which is what Q-45 would have required.
- **Outcome:** Built and in production use. `src/lagmatrix/comovement.py`
  (14 tests), persisted via `upsert_comovement` into `moves_with` (23,857 edges
  as of 2026-09-04), read by `/followers`, and driving `CoMovementFollowers` as a
  third `CandidateSource`. Coverage is 1,709 symbols against the filings graph's
  76. Face validity on real data is the strongest evidence it works: PANW returns
  CRWD/FTNT/OKTA/TENB/ZS, PFG returns MET/LNC/PRU/VOYA/CNO/AMP, JBL returns
  AEIS/BHE/LRCX/KN/FN — sectors nobody encoded anywhere.
  **Corrected by D-100:** every `0.640` above is the figure as measured on
  2026-09-09 and is wrong. Eleven corrupt returns (>1000%) inflated it; the
  replication coefficient is **0.586**. The conclusion — co-movement replicates
  out of sample, next-day prediction does not — is unchanged. Read D-100 before
  quoting any number from this entry.
- **Status:** Accepted

### D-96 — A shocked symbol's own next move is a coin flip; show the distribution, not a direction
- **When:** 2026-09-09T07:35:00-05:00
- **Decision:** The leader carries its own probabilistic move on the page, stated
  as a **distribution centred on zero**, never as an expected direction.
- **Why:** measured on the D-93/D-95 split, 40,545 held-out episodes across
  2,153 symbols, thesis-signed market-excess in the symbol's own sigma units:

      h    disc mean   val mean   val sd    val n   val P(continue)
      1      -0.0442    -0.0137    1.802   40,545        48.3%
      3      -0.0324    -0.0041    1.447   40,545        48.7%
      5      +0.0046    +0.0151    1.426   40,545        49.4%
     10      +0.0021    -0.0741    1.401   40,545        48.0%

  After a >= 2σ move a symbol goes nowhere in particular: mean within ±0.015σ of
  zero at every horizon, sd ~1.4σ, and it continues in its own direction 48-49%
  of the time — very slightly *against* continuation. No monotone pattern by
  shock size either (2-2.5σ: 48.9%; 4-6σ: 52.2%; 6σ+: 49.6%). At n=40,545 this
  is well powered, not a shrug.
  The honest presentation is therefore the spread, not a point: "after moves this
  size this name has historically gone nowhere in particular — 68% of outcomes
  within ±1.4σ, continuing 48% of the time." That tells a reader not to chase,
  which is real information, and it is the only probabilistic statement about a
  single symbol this data supports.
- **Outcome:** Done. Shown in the explorer beneath the follower list, as a
  distribution rather than a direction: "it continued in the same direction 48.7%
  of the time — a coin flip — with a spread of ±1.43σ around zero".
- **Status:** Accepted

### D-97 — The loaders drop collections; nothing may run daily until they upsert
- **When:** 2026-09-09T07:35:00-05:00
- **Decision:** Before any scheduled ingest, `load_vectors.py` and
  `load_arango.py` must become **incremental and non-destructive**: upsert by
  stable key, never `_drop`. `load_news.py` is already correct and is the model.
- **Why:** found while scoping the daily pipeline.
  `load_vectors.py:84` runs `if (db._collection("article")) { db._drop("article"); }`
  and `load_arango.py:89` does the same for its collections — full rebuilds, not
  updates. `load_arango.py:85` already carries the comment recording that this
  exact pattern "destroyed 47,640 embeddings and their vector index". Scheduling
  them daily would re-embed the entire corpus every night and drop live
  collections each time, converting a one-off accident into a nightly one.
  `load_news.py` shows the shape that works: stage into a temp table, merge with
  `ON CONFLICT DO NOTHING`, record coverage per (symbol, window) so a resumed run
  skips what it has. That is what the other two need.
- **Outcome:** Done, in `43d424e`. Both loaders create-if-absent and never drop;
  `grep -n '_drop' scripts/load_arango.py scripts/load_vectors.py` now returns only
  docstrings explaining what was removed. Documents upsert on stable keys —
  articles on their Alpaca id, supply edges on `f"{supplier}->{customer}"`, and
  co-mentions likewise — so a re-run refreshes what it touched, and two filings
  restating the same relationship collapse to one edge instead of doubling the
  graph. The JS text and the document shaping are extracted as importable
  functions and tested directly (6 tests), since the
  `ssh -> kubectl -> arangosh` transport is not exercisable here (Q-43's gap).
  148 passed, 0 skipped.
- **Status:** Accepted

### D-98 — ArangoDB is a tunnelled k8s pod, not a local container; a reboot needs two commands
- **When:** 2026-09-09T08:20:00-05:00
- **Decision:** Record the recovery procedure in the log, because a host reboot
  breaks it silently and the failure mode looks like data loss.
- **Why:** the dev machine restarted mid-session and ArangoDB "disappeared". It
  had not. The database is a pod in a k8s cluster on another host and is reached
  through **two** forwarding hops, both of which die with the machine:

      remote:  kubectl -n lagmatrix port-forward deploy/arangodb 19999:8529
      local:   ssh -f -N -L 19999:127.0.0.1:19999 ridopark@192.168.10.123

  `docker ps` shows nothing relevant, which is what makes it look like the
  container was lost. After restoring both hops: `equity` 514, `supplies_to`
  818, `co_mentioned` 2,129, `article` 47,640 — everything intact, pod uptime
  33h. The command is documented at `scripts/capture_showcase.py:151`; this
  entry exists so it is also findable from the log.
  **The suite's behaviour during the outage was correct and worth noting**: the
  9 live tests skipped rather than failing, which is exactly the default Q-43
  chose for a machine with no tunnel, and `LAGMATRIX_REQUIRE_LIVE=1` is what
  turns that into an error when a database is expected. The co-movement work
  (D-95) reads parquet only, so `/movers` and `/followers` kept working
  throughout — worth knowing that the new direction has no hard dependency on
  the graph store being up.
- **Outcome:** Verified — restored and confirmed, 148 passed 0 skipped after.
  **It then died a second time, unprompted, within the hour**, which changed
  this from a reboot procedure into a script: `scripts/arango_tunnel.sh`,
  idempotent, safe to re-run.
  Two things learned the second time that the first recovery missed.
  **(1) `nohup` is not enough on the remote** — the `kubectl port-forward`
  did not survive its ssh session closing; it needs
  `setsid nohup … </dev/null &  disown`.
  **(2) A listening socket is not proof the path works.** The local ssh tunnel
  stays up and keeps accepting connections after the remote forward dies, so
  `serve.arango_db()`'s TCP pre-check reported "reachable" while every query
  aborted with `ConnectionAbortedError`. The script probes the HTTP endpoint
  instead. That pre-check in `serve.py` has the same weakness and is left
  alone for now — it degrades to an empty follower count rather than a crash,
  and `/followers` is unaffected because co-movement reads parquet.
- **Status:** Accepted

### D-99 — Pre-registration: after a shock, do the shocked name's co-movement partners move the next day?
- **When:** 2026-09-09T12:10:00-05:00
- **Decision:** Test the one form of the lag hypothesis the earlier tests did not
  cover, specified **before any result is seen**.

  **Why this is not already answered.** D-93 tested lagged correlation
  *unconditionally* over all 2.47M pairs and found nothing. D-96 tested what the
  *shocked symbol itself* does next and found a coin flip. Neither tested the
  conditional, partner-directed version: *given X moved >= 2σ on day t, do the
  names that reliably move WITH X move on day t+1?* An unconditional null can
  coexist with a tail effect that only fires after large moves, so this is a
  distinct hypothesis and worth one test.

  **Population.** Every shock episode in `data/bars-10y.parquet` (2,183 symbols,
  2,514 sessions): a non-overlapping 3-session window with `|z| >= 2.0` against a
  60-session baseline — the same definition `MarketScan.shocked_leaders` uses.
  Partners are that symbol's co-movement edges at `|corr| >= 0.5` measured over
  the 250 sessions ending **strictly before** the episode (D-16; the partner set
  is chosen on past data only, so nothing is selected on the outcome).

  **Response.** Each partner's market-excess return on the **single session after
  the episode window**, normalised by its own trailing volatility, and signed to
  the thesis: `sign(z_X) * sign(corr)` — a negatively correlated partner is
  expected to move the other way, and gets the opposite sign.

  **Control.** For every episode, an equal number of symbols drawn at random from
  the same session's universe that are **not** partners of X, measured
  identically. This is the D-88 lesson: without a control, any market-wide day
  looks like an effect.

  **Primary test, one only.** Mean partner response minus mean control response,
  errors **clustered by date** (D-71 — every chain shares one market shock).

  **Economic threshold, declared now: >= 0.10σ.** Below roughly a tenth of a
  partner's own sigma (~0.2%) the move cannot survive a round trip, whatever the
  p-value.

  **Decision rule, pre-committed.** Claim next-day propagation only if the
  partner-minus-control difference is `>= 0.10σ` **and** `z >= 2`. Anything else
  is a null, including the right sign below threshold.

  **Power, reported with the result.** The run states its realised MDE. If the
  MDE exceeds 0.10 the test could not have found what it sought and is reported
  as underpowered rather than null — the distinction D-92 had to make and D-93
  was designed to avoid.
- **Why:** the alternative was answering from the earlier nulls by analogy. That
  would have been wrong: the conditional hypothesis is genuinely untested, the
  machinery to test it already exists, and the cost is one script.
- **Outcome:** **NULL, and this one is adequately powered.** 1,316,228 partner
  observations against 1,351,974 controls, over **751 date clusters**,
  2017-09-07..2026-08-31:

      partner (next session)   -0.0001σ
      control                  -0.0006σ
      partner - control = +0.0005 (0.0174)   z = +0.03
      realised MDE = 0.0487σ   against a 0.10 threshold

  The difference is +0.0005σ — indistinguishable from zero — and the realised MDE
  is **half** the pre-declared threshold, so an effect of tradeable size could not
  have been missed. This is not an underpowered shrug like D-92.
  **Getting here required two data bugs to be found first**, and both are worth
  recording because the first run reported a mean of 11,612σ:
  (1) the eleven >1000% returns of D-100; and (2) symbols whose trailing
  volatility is essentially zero — `EVER` sat at **3.67e-11** for stretches of
  2018, so market-excess subtraction alone gave it a "response" of 593 million
  sigma. A `sd > 0` guard is not sufficient; the script now floors volatility at
  0.1%/day. Note the second bug was invisible to a check on *raw* returns, whose
  worst `|r/vol|` is a harmless 7.75 — it only appears once the market is
  subtracted.
  This makes **four** pre-registered tests of the lag hypothesis, all null:
  D-59/D-60 (intraday), D-73/D-74 (supply chain, t+1), D-93/D-94 (all pairs, all
  lags, out of sample), and now D-99 (shock-conditional partners, t+1). The
  conditional form was the last shape that could plausibly have hidden an effect
  the unconditional tests missed. It does not.
- **Status:** Accepted

### D-100 — Eleven corrupt returns inflated D-95's headline from 0.586 to 0.640
- **When:** 2026-09-09T12:30:00-05:00
- **Decision:** Correct the record: co-movement's out-of-sample replication is
  **0.586**, not the 0.640 published today, and screen implausible returns inside
  `comovement_edges` rather than only in one-off analysis.
- **Why:** `data/bars-10y.parquet` holds **11 returns with `|pct_change| > 10`** —
  GPOR +526x (2021-05-18), LINE +448x (2024-07-25), SN +116x, DBD +81x, VAL +71x,
  CORZ +45x, and five smaller. Every one is a bankruptcy emergence, reverse split
  or ticker reuse: a price-series discontinuity, not a return. They are not
  outliers to respect, they are wrong numbers.
  Found while debugging D-99, whose first run produced mean responses of
  **11,612σ** — a 526x return divided by a 2% volatility. That absurdity was the
  symptom; the same rows had been sitting quietly inside every other measurement
  taken today.
  Re-running D-95's exact procedure with those 11 rows masked:

      as measured today (D-95)        1,236,371 pairs   corr(disc,val) = 0.640
      with corrupt returns removed    1,236,372 pairs   corr(disc,val) = 0.586

  **The conclusion is unchanged and still comfortable** — 0.586 against 0.03 for
  the lagged version is the same qualitative gap, and every sector result
  (PANW→CRWD/FTNT, PFG→MET/LNC/PRU) is unaffected because none of the 11 rows
  falls in a recent 250-session window. But the number quoted on the live page
  and in D-95 is inflated by data errors and must be corrected rather than
  quietly left.
  The alternative was fixing it only in the experiment scripts. Rejected: the
  production `comovement_edges` reads the same file, so any `as_of` whose
  trailing window spans one of those dates would carry the same distortion into
  a stored edge. The screen belongs in the module.
- **Outcome:** Done. The screen is in `comovement_edges` (`8a843f2`), and the
  figure is corrected to **0.586** everywhere it was published — `serve.py`,
  `comovement.py`, `adapters/candidates.py` and three places on the live page.
  The plan documents keep the old number, since they are a record of what was
  believed when they were written.
  The page also now *explains* the correction rather than silently swapping the
  digit: a showcase built on measurement discipline should show its own number
  moving. Verified in the browser — the only two remaining "0.64" mentions are
  inside that explanation.
  Production impact was small and checked: 23,855 edges against 23,857, 3.8s,
  and PANW/PFG return the same sectors.
  A second data fault was found in the same investigation and is recorded under
  D-99's Outcome rather than here: symbols whose trailing volatility is
  effectively zero (`EVER` at 3.67e-11), where market-excess subtraction alone
  produced a 593-million-sigma "response". That one bites any volatility-
  normalised statistic and is **not** screened inside `comovement.py`, because
  correlation is scale-invariant and therefore immune to it — but any future
  code that divides by a trailing sigma needs a floor, not a `> 0` check.
- **Status:** Accepted

### D-101 — fastembed replaces sentence-transformers: identical vectors, 1.2 GB less image
- **When:** 2026-09-09T23:10:00-05:00
- **Decision:** Swap `sentence_transformers` for `fastembed` (ONNX/onnxruntime) in
  `adapters/vector.py`, `scripts/load_vectors.py` and `scripts/capture_showcase.py`,
  so the deployable image does not carry torch.
- **Why:** `adapters/vector.py:16` imports `SentenceTransformer` at module scope,
  so *any* import of the graph pulls torch — 1.2 GB inside a 6.0 GB venv. The
  homelab node is shared and memory-tight (the sibling repo's manifest records
  it at "~87% on limits"), and that repo already solved the same problem the
  same way, noting the node "may have no egress".
  The risk that had to be cleared first was **embedding-space compatibility**:
  the 47,640 stored vectors were produced by
  `sentence-transformers/all-MiniLM-L6-v2`, and a replacement that embedded into
  a different space would silently make the corpus unsearchable rather than
  fail loudly. Verified against the live data rather than assumed — 40 real
  articles pulled from the `article` collection, each embedded with both
  encoders and compared to its own stored vector:

      fastembed vs sentence-transformers : min 1.000000  mean 1.000000
      fastembed vs STORED vectors        : min 1.000000  mean 1.000000
      sentence-transformers vs STORED    : min 1.000000  mean 1.000000

  Identical to six decimal places: same ONNX weights, same mean pooling, both
  L2-normalised 384-dim. This is a runtime swap, not a corpus migration, and
  `APPROX_NEAR_COSINE` results are unchanged because the query vector is
  bit-comparable.
  Two alternatives lost. Keeping torch and accepting a ~2 GB image: rejected
  because the constraint is the node's memory, not the registry's disk.
  Splitting into a torch-free web image and a torch-carrying ingest image:
  rejected as unnecessary once the dependency is gone entirely — though whether
  web and ingest still warrant *separate* images for credential and PVC-write
  reasons is a live question for the deployment plan, not settled here.
- **Outcome:** **Implemented 2026-09-10**, and the compatibility claim now holds
  inside this repo rather than in a scratch venv: fastembed reproduces the
  **stored** vectors at cosine **1.000000** across 8 real articles, unit norm,
  so the 47,829 embeddings already in the corpus stay valid and nothing needs
  re-embedding. `torch` and `sentence_transformers` are both absent from
  `sys.modules` after importing `lagmatrix.adapters.vector`, and neither name
  appears in `src/`, `scripts/` or `tests/` outside the test that must name them
  to check for them. 248 passed.
  **Measured, not estimated:** `.venv` went **6.0G → 574M**, about 10.5x — the
  lockfile dropped torch, transformers, scipy, scikit-learn, sympy, triton and
  the nvidia-cu13 wheels, and gained fastembed, onnxruntime, pillow, mmh3 and
  py-rust-stemmers. That is larger than this entry's own "~7x" figure, which
  counted dependency weight rather than resident venv size.
  A detail nobody had recorded, found while writing the test: the stored vectors
  were built from `headline + ". " + summary[:600]` (`load_vectors.py:107`), not
  from headlines. Embedding a headline alone reproduces the stored vector at only
  0.89–0.99. The `600` is load-bearing — change it and every future embedding
  silently stops matching the corpus, with no error anywhere.
  Originally: pending — verified compatible, not yet implemented; TDD, and the
  red test must pin agreement with the *stored* vectors rather than merely that
  the module imports. Confirmed still unimplemented on 2026-09-09: `uv.lock` has
  zero `fastembed` entries and two `torch` ones, `pyproject.toml:22` still pins
  `sentence-transformers>=6.0.1`, and `adapters/vector.py:16` still imports
  `SentenceTransformer` at module scope. **Consequence for deployment:** the
  homelab plan's halt condition — stop if `torch` is in `sys.modules` after
  importing `lagmatrix.adapters.vector` — fires on the first image build. The
  guard is working as designed; the point is that the build blocks on this,
  and that is better known now than at build time.
- **Correction to the reasoning above,** on two points others measured after
  this entry was written. (1) "The constraint is the node's memory, not the
  registry's disk" is **backwards**. torch costs about **104 MiB resident**, not
  1.2 GB — the 1.2 GB is on-disk venv weight. The case for dropping it is image
  size and pull time on a home-network-connected node, plus the roughly 7x cut
  in dependency weight; it is not a memory argument. (2) The cited "~87% on
  limits" from the sibling repo's manifest is **stale**. Measured directly:
  memory limits sum to **114% of the node**, actual usage 79%, about **3.2 GiB
  genuinely free**, on a single-node k3s box carrying 41 pods across 10
  namespaces. The conclusion — drop torch — survives both corrections; the
  argument for it does not.
- **Status:** Accepted

### D-102 — One unauthenticated GET can exhaust the node; request parameters get clamped
- **When:** 2026-09-09T23:40:00-05:00
- **Decision:** Clamp `min_abs_corr`, `top_n` and `limit` at the HTTP handlers,
  **and** enforce a floor inside `comovement_edges` itself so no caller — not
  just no HTTP caller — can request the full pairwise matrix. Reject non-finite
  input rather than clamping it.
- **Why:** found by a security review of the deployment, and verified directly.
  `serve.py:569` passes `min_abs_corr` through a bare `float()` with no bound.
  `comovement.py:88` filters on `abs(c) < min_abs_corr`, so:

      min_abs_corr=0.5  ->     23,855 edges,   3.9s
      min_abs_corr=0    ->  2,381,653 edges, 100.5s, peak RSS 3,560 MB

  and **`min_abs_corr=nan` is identical to 0**, because `abs(c) < nan` is always
  `False` — confirmed against `corr=0.0001`, where `0.0`, `-1.0` and `nan` all
  keep the edge and only `0.5` drops it. `ThreadingHTTPServer` spawns a thread
  per connection, so concurrent requests multiply it linearly.
  **The reason this is not merely a performance bug:** the target is a
  single-node cluster that also runs the owner's real-money trading system in
  namespace `copytrade`. A namespace is not a memory or CPU boundary — driving
  the node to OOM-kill degrades or evicts the trading pods regardless. The
  comment at `infra/k8s/10-arangodb.yaml:5` claiming its own namespace means
  "nothing here can touch the trading workloads" is **false for CPU and memory**,
  and should be corrected when that manifest is next touched.
  Two mitigating facts, recorded so the risk is not overstated: nothing is
  exposed today (the server binds localhost and no Service exists yet), and the
  HTTP surface never writes to ArangoDB — `upsert_comovement` is reachable only
  from `daily_ingest.py`. What raises the stakes is `serve.py:116-117`: the
  process holds the ArangoDB **root** password and connects as root, so anything
  achieving code execution in the pod gets root on the database.
  The alternative was relying on k8s `limits` alone. Rejected as insufficient on
  its own: a limit converts node-wide exhaustion into a pod OOM-kill, which is
  better but still a self-inflicted outage on every request, and it does nothing
  for a caller who is merely careless rather than hostile. Both layers, not one.
- **Outcome:** pending — TDD in flight; the test must assert the edge *count*
  stays bounded, since that is the property under attack.
- **Status:** Accepted

### D-103 — A value stored in ArangoDB reached psql inside the trading namespace
- **When:** 2026-09-09T23:55:00-05:00
- **Decision:** Validate `--since` as a date at **both** ends of the ingest loop,
  validate tickers before interpolating them, and add the missing
  `ON_ERROR_STOP=1` so `load_vectors.py` matches the other two loaders.
- **Why:** found by a security review of the deployment and verified line by
  line. `load_vectors.py:82` built SQL by f-string —
  `WHERE a.created_at >= '{args.since}'` — and piped it to
  `kubectl -n copytrade exec -i postgres-0 -- psql -U temporal -d orchestrator`.
  That is the **trading system's own database, in another namespace**.
  `--since` is not always operator-typed. `daily_ingest.py:107-109` sets it from
  `last_article_date()`, which reads it back out of ArangoDB
  (`FOR a IN article COLLECT AGGREGATE hi = MAX(a.date) RETURN hi`). So anything
  written into `article.date` became SQL executed as `temporal` against
  `orchestrator` — a second-order injection whose source is our own datastore.
  Aggravating: `load_vectors.py` was the **only one of three loaders without
  `-v ON_ERROR_STOP=1`** (compare `load_arango.py:35`, `load_news.py:38`), so an
  injected statement would not abort and the job would report success.
  Reachability, stated honestly rather than dramatised: it needs ArangoDB
  **write** access first, and the HTTP surface has none — `upsert_comovement`
  is reachable only from `daily_ingest.py`, and `ArangoTopology.upsert_edge` is
  `NotImplementedError`. So it is not remotely triggerable today. It is the
  mechanism that would convert a `lagmatrix` foothold into SQL execution inside
  `copytrade`, which is precisely the adjacency that made the review worth doing.
  **I introduced the reachable half of this today** when `daily_ingest.py` began
  feeding a database-derived value into a script that had always interpolated it.
  The script's f-string predates the ingest job; what was new was the loop that
  closed it.
  The alternative was validating only in `load_vectors.py`, at the point of use.
  Rejected: `daily_ingest.py` also validates now, because a value coming back out
  of a datastore deserves the same suspicion as one arriving from a user, and
  trusting it merely because we wrote it is the assumption that created this.
- **Outcome:** Fixed and verified. `date.fromisoformat` rejects
  `"2026-09-07'; DROP TABLE x; --"`, `"2026-09-07 OR 1=1"` and `""`, accepting
  only a real ISO date; tickers must match `[A-Z][A-Z.\-]{0,9}`; all three
  loaders now carry `ON_ERROR_STOP=1`. 170 passed, baseline unchanged, ingest
  dry-run still resumes correctly from 2026-09-07.
  Two things deliberately NOT changed here, recorded so they are not lost:
  `serve.py:116` still connects to ArangoDB as **root** when the serving process
  needs only reads (a dedicated read-only grant is the right fix and is its own
  change), and there is no NetworkPolicy restricting who may reach
  `arangodb.lagmatrix:8529`.
- **Status:** Accepted

### D-104 — An unknown `source` returned invented data instead of an error
- **When:** 2026-09-09T18:20:00-05:00
- **Decision:** `load()` names `synthetic` explicitly and raises `ValueError` at
  the end of the function. A `leader:` symbol is checked for shape and for
  presence in the price file before it reaches pandas.
- **Why:** `scripts/serve.py` ended `load()` with an unguarded
  `return closes, ExternalSignals(SYNTHETIC_FIRES).candidates(), frozenset()`.
  There was **no `if source == "synthetic"` anywhere** — synthetic data was
  reached only by falling off the end, so `'nonsense'`, `'leader'` without a
  colon, and `''` each returned six synthetic candidates as though the caller
  had got what they asked for. Measured, not inferred, before any test was
  written. Separately, `'leader:ZZZZZZ'` and `'leader:A B'` reached a pandas
  column lookup and raised `KeyError: "None of [Index(['ZZZZZZ'], ...)] are in
  the [columns]"` — an internal leaking out as a generic failure. Two failure
  shapes, and the silent one is the worse: a typo in `?source=` produced a
  plausible-looking run over data nobody asked for. Same class as D-100 and
  Q-43 — a wrong answer delivered quietly instead of an error delivered loudly.
  The alternative that lost was blocklisting the known-bad strings while keeping
  the fallback; a test probing `'synthetic '` with one trailing space forecloses
  it, since that matches no branch either and would have kept returning
  synthetic data. The leader shape check reuses the `.isalnum()` rule
  `/followers`, `/network` and `/graph` already applied (`serve.py:553-554`,
  `:565-566`, `:578-579`); `/run` was the one entry point without it, and that
  inconsistency is what started the cycle.
- **Outcome:** 198 passed. Verified against a live server rather than by
  reading: `/run?source=nonsense` and `?source=leader:ZZZZZZ` and
  `?source=leader:AB;DROP` each return a clean SSE `event: error` naming the bad
  value, with no traceback and no pandas internals; `?limit=99999` returns 400;
  `?source=leader:PANW&as_of=2026-09-04` still streams CRWD/FTNT/OKTA over 36
  events with zero errors.
- **Status:** Accepted

### D-105 — `/network`'s `top_n` is deliberately not readable from the query
- **When:** 2026-09-09T18:22:00-05:00
- **Decision:** Reverted, hours after adding it. `/network` keeps its default of
  40 and ignores `top_n` in the query string; the parameter stays wired on
  `/followers`, where a client actually sends it.
- **Why:** my own brief for D-102 listed `top_n (1, 500)` under `/network`
  without checking whether the handler read it. It did not — it silently used
  the function default. Wiring it made a previously unreachable parameter
  reachable. Checked against the actual client afterwards:
  `serve_index.html:1091` sends `top_n` to `/followers`, `:1292` sends it to
  `/movers`, and `:1274` sends `/network` only `symbol`, `as_of` and
  `min_abs_corr`. So nothing wanted it, and exposing it hands an
  unauthenticated caller a lever to make an endpoint that does real correlation
  work build a 500-node graph where it could previously build 40 — widening
  surface in the same week the exposure of this service is under review. The
  alternative that lost was keeping it because it was already bounded and
  tested. Bounded is not the same as warranted.
- **Outcome:** Reverted with a comment at the call site saying why, so it is not
  re-added by someone reading the other two handlers. 198 passed.
- **Status:** Accepted

### D-106 — Throwaway test databases are per-process, answering Q-46
- **When:** 2026-09-09T18:24:00-05:00
- **Decision:** `arango_db_or_skip` suffixes every throwaway database with the
  xdist worker id or the pid, and a `pytest_sessionfinish` hook drops the ones
  this process created.
- **Why:** Q-46 logged this as real but declined to fix it, on two grounds that
  both turned out to be wrong. It said the collision was "**not** reproducible
  in normal use" — it reproduces on demand. Two suites started together gave
  `6 failed, 184 passed, 8 errors` and `3 failed, 195 passed`, every failure in
  a live-ArangoDB file, while either run alone was green. And it estimated "the
  fix touches five files"; it touches one, because the per-process suffix
  belongs in the shared helper rather than in each file's `ARANGO_DB_NAME`.
  The five files hardcoded `test_comovement_store`, `test_arango_topology`,
  `test_vector_index`, `test_market_scan`, `test_loader_idempotency`, and each
  fixture truncates its collection per test — so a second process's truncate
  lands in the middle of the first's test. Found by running the suite while an
  agent was running it too, getting one failure that passed in isolation, and
  reproducing it deliberately instead of writing it off as a flake. That is the
  cost being paid: a shared name makes the suite report failures unrelated to
  the code under test, which teaches you to distrust red. Q-43 fixed the mirror
  image — tests green while verifying nothing.
- **Outcome:** Three concurrent suites: 198, 198, 198, zero failures. Teardown
  confirmed against the live instance — the 15 scoped databases those runs
  created are gone, `lagmatrix` untouched. A guard rejects any name not starting
  with `test_`, so a typo can never point the suite at the real database. The
  five old fixed-name databases are now orphaned and were left in place rather
  than dropped unasked.
- **Status:** Accepted


### D-107 — A mistyped `as_of` on `/graph` returned filings from years later
- **When:** 2026-09-09T18:40:00-05:00
- **Decision:** `neighbourhood()` parses `as_of` with `date.fromisoformat`
  before it reaches ArangoDB — one line, the same idiom `movers()`,
  `followers()`, `network()` and `load()` already used.
- **Why:** `neighbourhood()` was the only endpoint that took `as_of` as a
  string and never parsed it. The AQL compares dates as strings
  (`capture_showcase.py:54`, `FILTER p.edges[*].filing_date ALL <= @as_of`),
  which is correct against ISO input and arbitrary against anything else.
  Measured against the live graph, WMT at hops 2, **before** the fix:

      as_of='2019-01-01'  (correct ISO)   ->  2 edges   <- the truth
      as_of='2019-1-1'    (unpadded)      ->  8 edges
      as_of='Jan 1 2019'                  -> 13 edges   <- the whole 2026 graph
      as_of='2026-09-04'  (today)         -> 13 edges

  `2019-1-1` is an ordinary way to type a date, and it silently returned
  filings from years **after** the date asked about. That is a lookahead bug —
  D-82's exact class — reachable from the UI's own `/graph` endpoint, and it
  violates D-16 ("point-in-time by construction"). It produced no error and no
  warning; the page would simply have shown a richer 2019 than 2019 had.
  Found by a deployment security review that flagged the missing validation as
  a hardening gap. It is **not** primarily a security issue — an attacker gains
  nothing they could not get by passing today's date. It is a **correctness**
  issue, and filing it as hardening would have understated it.
  The alternative that lost was validating in the `/graph` handler alongside
  its existing `.isalnum()` check. Rejected because `neighbourhood()` is the
  thing that must not query on a bad date; a guard in the caller leaves the
  function itself unsafe. A test spies on `db.aql.execute` and asserts it is
  never reached, so "rejects" means "before querying", not merely "raises".
- **Outcome:** 207 passed. Verified live after the fix: `2019-01-01` returns
  2 edges (HRL, TSN), `2026-09-04` returns 13, and both `2019-1-1` and
  `Jan 1 2019` return a clean error instead of 8 and 13.
- **Status:** Accepted

### D-108 — Errors detectable before the response begins are a 400, answering Q-47
- **When:** 2026-09-09T18:50:00-05:00
- **Decision:** `/graph` and `/movers` catch `ValueError` and `send_error(400)`
  before their general handler, matching what `/followers` and `/network`
  already did.
- **Why:** the rule, stated once so it stops being re-derived per endpoint:
  **an error detectable before `send_response` is called is a 400.** `/graph`
  and `/movers` build their whole body in memory first, so a `ValueError` there
  can still choose its status — they were routing it through `_json`, which
  hardcodes `send_response(200)`, so D-107's new malformed-`as_of` error came
  back as 200 carrying an error body and a status-only client could not tell bad
  input from a clean run.
  `/run` is **not** an exception to this rule, which is the part I had wrong
  when I framed the cycle. `red-status` corrected it: `/run`'s `limit` check
  already 400s *because* it runs before the SSE headers are flushed, and only
  what `stream()` discovers afterwards is structurally stuck in the body. One
  rule, no carve-out.
  The catch is deliberately narrow — `ValueError`, not `Exception`. An ArangoDB
  outage or a missing parquet file is not bad input and must keep its existing
  path. Verified by exercising a genuine non-input failure: a server started
  without `--allow-real` raises `PermissionError` from `movers()`, and that
  still returns 200 with an error body, unchanged.
- **Outcome:** 216 passed. Live: `/graph?as_of=2019-1-1` and `/movers?as_of=zzzz`
  both 400; `/graph?as_of=2019-01-01` returns its real 2 edges (HRL, TSN) with
  disclosure sentences and 7 two-hop entries; `/movers?as_of=2026-09-04` returns
  44 movers found from 3,201 swept.
- **Status:** Accepted

### D-109 — `default_as_of()` derives from the frame co-movement actually reads
- **When:** 2026-09-09T19:05:00-05:00
- **Decision:** `default_as_of()` returns `COMOVE_CLOSES()`'s last session
  instead of re-reading `data/bars.parquet` itself.
- **Why:** the two disagreed, and the disagreement was invisible. `default_as_of()`
  read `bars.parquet`; `COMOVE_CLOSES()` prefers `bars-10y.parquet`. They agreed
  by coincidence until the first real `daily_ingest.py` run advanced one file and
  not the other, and then:

      default_as_of()                    -> "2026-09-09"
      followers("PANW", "2026-09-04")    -> 13 followers
      followers("PANW", default_as_of()) ->  0 followers, error=None

  The page's default date silently returned nothing. Its docstring claimed it
  returned "the last session actually present in the data" — it returned the last
  session present in *a* file, not the one that answers the question, and the
  docstring is corrected to say what it now guarantees.
  Deriving from `COMOVE_CLOSES()` makes the two agree **by construction**. The
  alternative that lost was giving `default_as_of()` its own corrected file
  preference: that restores agreement by coincidence again, and coincidence is
  precisely what broke.
  **Measured cost, since this moves a large read onto a hot path.** `/config`
  fires on every page load, so the co-movement frame is now read there rather
  than only when a co-movement feature is invoked. On real data:

      RSS before any request   155.8 MiB
      first /config  (cold)    200 in 1.280s  -> 337.6 MiB   (+182 MiB)
      second /config (warm)    200 in 0.0016s

  One-time per process and cached. Note the +182 MiB is the *steady* cost; it is
  not the 650 MiB–1,030 MiB figure the deployment work uses, which is peak during
  the pivot. Both numbers are real and they measure different things — worth
  keeping straight, since a pod limit has to cover the peak.
- **Outcome:** 220 passed. `default_as_of()` returns 2026-09-04 and
  `followers("PANW", default_as_of())` returns 13 with no error. This is PHASE-2
  of `PLAN-2026-09-09-ingest-coherence.md`, executed ahead of the rest of that
  plan because the broken default date was live and user-visible.
- **Status:** Accepted

### D-110 — The ingest refuses to claim success when its data cannot answer
- **When:** 2026-09-10T00:10:00-05:00
- **Decision:** `session_available(closes, as_of, trail)` in
  `src/lagmatrix/comovement.py`, checked by `node_comovement` **before**
  `comovement_edges` runs. A failure returns an `errors` entry naming both dates
  and never reaches `upsert_comovement`.
- **Why:** tonight's first real ingest advanced `bars.parquet` to 2026-09-09 and
  left `bars-10y.parquet` — the file co-movement reads — at 2026-09-04. Tomorrow's
  run would have asked for a session that file does not contain, received `[]`,
  written nothing, and printed:

      {'done': ['comovement: 0 edges upserted as of 2026-09-09']}

  Reproduced on synthetic data as the red test, then verified in-process against
  the real frame. After the fix, the same call returns:

      {'errors': ['comovement: 2026-09-09 not found in data
                   (last session available: 2026-09-04)']}
      upsert_comovement called: []

  and a good date still returns 23,855 edges upserted, unchanged.
  **The distinction this deliberately preserves.** A quiet trading day producing
  zero edges from a *fully available* window is a real result, not a failure.
  `session_available` runs before `comovement_edges` and therefore cannot see the
  edge count, so the two cases cannot be conflated. The alternative that lost was
  checking `len(edges) == 0` after the fact, which is simpler and wrong — it would
  trade a silent wrong answer for a noisy false one.
  Additive by design: `comovement_edges`'s existing return-`[]` contract is
  untouched, so a later, wider change to that contract in the deployment work
  cannot collide with this.
- **Outcome:** 230 passed. PHASE-1 of `PLAN-2026-09-09-ingest-coherence.md`.
  Does **not** by itself make tomorrow's run correct — it makes tomorrow's run
  *fail loudly* instead of silently. PHASE-3 and PHASE-4 are what actually keep
  the long file current.
- **Status:** Accepted

### D-111 — An unreachable database is not "start from the beginning"
- **When:** 2026-09-10T00:45:00-05:00
- **Decision:** `last_article_date()` returns `(ok, since, reason)`. `node_news`
  and `node_vectors` both refuse to run when `ok` is false, and neither reaches
  its subprocess.
- **Why:** the function collapsed three situations into `None` — ArangoDB
  unreachable, `article` genuinely empty, and a stored date failing
  `date.fromisoformat` — and both callers read `None` as "start from scratch".
  `node_news` dropped `--start` and fetched ten years across 500 symbols;
  `node_vectors` fell back to a hardcoded `2025-01-01` and re-embedded
  everything since. Both then reported `done`.
  Found by comparing two `--dry-run` outputs minutes apart: one printed
  `news: from the beginning`, the other `news: from 2026-09-09`. The only
  difference was that ArangoDB was unreachable for the first. **Not a
  hypothetical** — Q-49 records the pod being OOM-killed 8 times in two days, so
  an unattended 3am run will meet a dead database.
  The empty-collection case still starts from the beginning, because a genuine
  first run must. Two negative controls pin that, and they are the reason the
  fix could not simply be "error on any falsy result".
  The outer `except Exception` is gone with it. Previously a failure *during*
  the query — which is exactly what an OOM-kill mid-query looks like — was
  swallowed into the same silent refetch. It now propagates, is retried by the
  graph's `RetryPolicy(max_attempts=3)`, and then fails.
  `node_vectors` was nearly missed: it calls the same function at a second call
  site that no test covered, and a tuple is always truthy, so the change would
  have left `since or "2025-01-01"` silently dead. The red agent flagged it
  rather than leaving it for green to notice.
- **Outcome:** 245 passed. All three data nodes — `comovement` (D-110), `news`
  and `vectors` — now fail loudly on the same class of condition rather than
  proceeding on a guess.
- **Status:** Accepted

### D-112 — Two bars files, kept apart on purpose
- **When:** 2026-09-10T00:55:00-05:00
- **Decision:** `data/bars.parquet` and `data/bars-10y.parquet` stay separate.
  The wide file is refetched whole each run; the long file is extended
  incrementally with a corporate-actions split guard.
- **Why:** the obvious simplification is one file, and it loses on two counts.
  They differ in *history* (159 vs 2,514 sessions) and in *universe* (3,201 vs
  2,183 symbols, the wide one drifting with a liquidity screen, the long one
  pinned for reproducibility — D-95's replication is measured against that fixed
  set). Merging would either impose the long file's cost on every wide read or
  the wide file's drift on the replication baseline.
  It also turns out the split immunity is a property of *how* each is fetched,
  not of anything either file does: `fetch_bars.py` never reads the existing
  file, so every row it writes shares one adjustment basis. The long file cannot
  afford that — a nightly 10-year, 2,183-symbol refetch — which is exactly why
  it needs the split guard the wide one does not.
  Closes `PLAN-2026-09-09-ingest-coherence.md`. The plan completed a spec that
  had already been written and skipped once: `PLAN-2026-09-09-daily-ingest.md`
  PHASE-2 defined `merge_bars`/`daily_watermark`/`fetch_daily_bars.py` and none
  of it existed. That is the same pattern as D-101 (verified, never
  implemented) and the ten plans that carried no status line until today — work
  recorded as done that was not.
- **Outcome:** Verified by a real run, not a dry one. `bars-10y.parquet` went
  2026-09-04 → 2026-09-09 (+4,366 rows), APH and RUSHA were refetched for live
  splits, `as_of` resolved to 2026-09-09 *after* the fetch, and co-movement
  wrote 24,104 edges for that date. `default_as_of()` now returns 2026-09-09 and
  `followers("PANW", ...)` returns 11 — CRWD, FTNT, OKTA, TENB, ZS, S.
- **Status:** Accepted

### D-113 — `/health` reports the process, never its dependencies
- **When:** 2026-09-10T01:30:00-05:00
- **Decision:** `/health` returns `{"status": "ok"}` unconditionally — no
  ArangoDB, no parquet, no `--allow-real`. Readiness stays on `/config`, which
  already reports `graphrag: arango_db() is not None` non-fatally. No second
  endpoint.
- **Why:** a liveness probe that fails on a *dependency* outage gets the pod
  killed for something a restart cannot fix, and Q-49 shows that outage is
  routine rather than hypothetical. Separately, `default_as_of()` now routes
  through `COMOVE_CLOSES()` (D-109), a ~650MiB read — a probe touching that
  every 10 seconds would be its own outage. The tests enforce both by
  monkeypatching `arango_db`, `COMOVE_CLOSES` and `read_parquet` to **raise**,
  so a future `/health` that reaches a dependency fails loudly rather than
  merely getting slow.
- **Outcome:** 251 passed. Container verified, not just built:
  `curl /health` → `{"status": "ok"}` 200, and `docker ps` reports
  `Up 34 seconds (healthy)`.
- **Status:** Accepted

### D-114 — `serve.ALLOW_REAL` is restored after every test
- **When:** 2026-09-10T01:30:00-05:00
- **Decision:** an autouse fixture in `tests/conftest.py` saves and restores
  `serve.ALLOW_REAL` around every test.
- **Why:** it is module-level global state and several tests flip it to `True`
  to reach real-data paths. **None restored it**, so whether a test depending on
  the default (`serve.py:93`, `ALLOW_REAL = False`) passed came down to
  alphabetical file order. Caught when `tests/test_serve_health.py` passed alone
  (3 passed) and failed in the full suite — the failure looked like a bad
  `/health` implementation and was not. Autouse rather than a per-test
  `monkeypatch` because the point is that a test written next month cannot
  reintroduce it by forgetting; six existing call sites had already forgotten.
  Third isolation defect in the suite today, after Q-46 (concurrent runs sharing
  a database) and Q-43 (live tests skipping silently). Same root: shared mutable
  state with no one responsible for putting it back.
- **Outcome:** 251 passed, and the two pairings that previously failed —
  `test_serve_source.py` + `test_serve_health.py`, and
  `test_serve_default_as_of_coherence.py` + `test_serve_health.py` — now pass.
- **Status:** Accepted

### D-115 — The ingest is one ordered chain; D-89's independence claim is withdrawn
- **When:** 2026-09-10T10:15:00-05:00
- **Decision:** `daily_ingest.py` becomes a single chain —
  `extract_fires -> bars -> long_bars -> comovement -> news -> vectors` — and
  gains `extract_fires` as a node. D-89's *mechanism* stands; the *claim* that
  motivated its shape does not.
- **Why:** D-89 fanned two chains out from START and never joined them, on the
  stated grounds that "the two chains have nothing to say to each other". They
  do. `node_news` runs `load_news.py --top-liquid`, which reads the
  `data/bars.parquet` that `node_bars` writes, and both sat in the **same
  superstep**. Which version it read was decided by subprocess startup timing —
  and since `fetch_bars.py` fetches for minutes before writing, the read always
  won, so news ranked liquidity from the *previous* run's file. Not
  occasionally: `node_bars`' skip predicate (`last >= today - 1 day`) is
  evaluated before a fetch that ends at `now`, which on a 09:00 UTC schedule is
  before the US close, so the file sits permanently one session behind its own
  predicate and the node writes on essentially every scheduled run. Verified by
  simulating the predicate: run 09-10 skips, 09-11 and 09-14 fetch.
  **Correct by coincidence** — D-109's pathology, in the scheduler this time.
  Two consequences beyond the stale read. On an empty PVC `news` fails with
  `FileNotFoundError` and retries three times *inside the same superstep*,
  where `bars`' output cannot yet be visible, so the graph could never cold
  start. And `fetch_bars.py:104` wrote the file non-atomically, unlike
  `fetch_daily_bars.py`; that is now temp-file-then-rename, which closes the
  torn-read window and — stated at the call site — does **not** fix the
  ordering, because ordering is a graph problem.
  **Serial rather than the minimal `bars -> news` edge, for a measured reason.**
  Making `vectors` run after both `news` and `comovement` needs two in-edges,
  and D-89's mechanism says such a join fires twice when its in-edges land in
  different supersteps. Relying on superstep ordering instead would be correct
  by coincidence again. Keeping them concurrent put a measured 1,018 MiB
  (comovement) and 617 MiB (`load_vectors.py`) in one superstep at the moment
  `load_vectors` triggers ArangoDB's index rebuild — against ~2.8 GiB free on a
  node that also runs real-money trading. Serial peak is one node, ~1,018 MiB.
  The cost is wall-clock on a job that has all night.
  `extract_fires` is safe to run nightly for a structural reason:
  `fetch_daily_bars.py:97` takes the long file's universe from
  `existing["symbol"].unique()`, so it cannot reach D-95's frozen 2,183-symbol
  cohort. Two of the three consumers I believed it had do not consume it —
  `load_news.py:177` reads it only under `--from-fires`, and the force-include
  in `fetch_bars.py:101` is empirically a no-op, since the lowest fires ticker
  is PFE at $1.005B median dollar volume against a $10M threshold.
- **Outcome:** 266 passed. Dry run shows all six nodes in order. Found by
  consulting two agents about a much smaller question; neither of the two
  defects above was the question asked.
- **Status:** Accepted

### D-116 — D-95's split is pinned to a date; the fraction was sliding it
- **When:** 2026-09-10T10:35:00-05:00
- **Decision:** `experiment_lag_matrix.py` and `experiment_chains.py` split at
  `VALIDATION_START = 2022-09-06`, compared by session **date**, with four fatal
  preconditions. `scipy` becomes a declared dependency.
- **Why:** both split with `cut = int(len(rets) * 0.60)` — a fraction of however
  many sessions the file holds. D-115 gave `long_bars` the job of appending
  sessions nightly, so that boundary now **slides**: measured on the real file,
  +21 sessions moves discovery's end from 2022-09-02 to 2022-09-21, and +252
  (about a year) to 2023-04-12 — seven months. Re-running afterwards prints a
  number that looks comparable to 0.586 and is not, and with `*.parquet`
  gitignored and `fetch_daily_bars.py` rewriting split-affected history in
  place, no prior state is recoverable. The headline result was quietly becoming
  unfalsifiable, as a consequence of work done the same day.
  The date is derived, not chosen: 2,515 sessions, `int(2515*0.60) = 1509`,
  `rets.index[1509]`. Confirmed to reproduce D-95/D-100 across five quantities
  — 11 masked returns, 1,573 symbols, 1,236,372 pairs, 6 same-company drops,
  corr 0.5860 — which is a far stronger check than matching the headline alone.
  **Two of the four preconditions are the point.** Range and
  session-existence only catch a boundary that has gone missing. Discovery
  length (1,509) and discovery end (2022-09-02) catch history rewritten
  *underneath* a date that still exists — exactly what a split refetch does.
  Without those the script stays deterministic while silently measuring a
  different experiment.
  Validation's **end** is left open on purpose: a growing out-of-sample window
  is the one thing nightly ingest genuinely improves.
  Compared by session date rather than exact timestamp because the bars are
  stamped `04:00:00+00:00` (midnight ET). My first attempt pinned midnight UTC
  and the existence precondition caught it — the check firing on its author.
- **Outcome:** Reproduces. `sessions 2,515, discovery 1,509
  (2016-09-07..2022-09-02), validation 1,006 (2022-09-06..2026-09-09)`,
  **1,573 symbols** matching D-95, and lag 1 at **0.0281** matching D-93's
  stated 0.028. `experiment_chains.py` clears the same preconditions.
  **Incidental, and worse than the defect being fixed:** running it revealed
  D-101's fastembed swap had already broken both scripts entirely —
  `ModuleNotFoundError: scipy`. `scipy` was never declared; it arrived
  transitively via sentence-transformers, so removing torch removed it. Nothing
  caught this because the experiment scripts are in neither the test suite nor
  CI. It is now a direct dependency. **The reproduction scripts being outside
  every automated check is the underlying gap and is not fixed here.**
- **Status:** Accepted

### D-117 — The ingest runs in the cluster; Q-50 is answered
- **When:** 2026-09-10T12:20:00-05:00
- **Decision:** `lagmatrix-ingest` CronJob applied, `0 9 * * 2-6`, pinned to
  image `ghcr.io/ridopark/lag-equity-matrix-ai:99845ed`, against a `lagmatrix-data`
  PVC seeded once with `bars-10y.parquet`.
- **Why:** Q-50 recorded that nothing ran `daily_ingest.py` at all, which made
  every "the ingest now fails loudly at 3am" claim conditional on a run that
  never happened.
- **Outcome:** A real in-cluster run completed in 76s, all six nodes ok:
  fires refreshed (105 signals, 24 tickers), bars and long_bars current,
  **24,104 co-movement edges upserted as of 2026-09-09**, news at 230,317
  articles, 85 embeddings, 47,829 indexed. Identical to the local run.
  `article` 47,829 and `moves_with` 47,961, both unchanged.
  **Four failures got there, and each was invisible to every check that
  preceded it** — the image built, the manifests passed `--dry-run=server`, the
  suite was green, and the pipeline ran perfectly on a laptop:
  1. `LAGMATRIX_ARANGO_URL` unset, so `serve.py:94` defaulted to
     `http://localhost:19999` — a developer's SSH tunnel, meaningless in a pod.
  2. The `arangodb-auth` mount was `defaultMode: 0400` and Kubernetes owns
     secret volumes **root:root**, so uid 10001 could not read it. Fixed with
     `fsGroup: 10001` and mode 0440.
  3. The PVC mounts at `/app/data` and **shadows what the image baked there**,
     so the committed `data/excluded-etfs.csv` was invisible and `comovement`
     died on it. An init container now copies it onto the volume each run,
     keeping the repo as the source of truth rather than seeding a copy that
     can drift.
  4. `alpaca-credentials` was templated in `secrets-template.yaml` and never
     created. A template documents intent; it does not provision.
  **The diagnosability defect is the one worth keeping.** Failure 2 reported
  **"ArangoDB not reachable"**. The database was reachable throughout — the pod
  could not read the *password*. `serve.py:105`'s bare
  `except Exception: return None` conflates an unreachable socket with an
  unreadable credential, and reports the first. The message was loud and named
  the wrong cause, so it sent the search to the URL. This whole session has
  been about making failures loud; this one was loud and still misleading,
  which is a distinct and harder problem. Logged as Q-55.
- **Status:** Accepted

### D-118 — The web pod is deployed ClusterIP-only, connecting read-only
- **When:** 2026-09-10T12:45:00-05:00
- **Decision:** `lagmatrix-web` Deployment plus a ClusterIP Service, **no
  Ingress**, connecting as `lagmatrix_ro` rather than root.
- **Why:** auth gates *exposure*, not deployment, and conflating the two was
  holding up a change that improves security on its own. `serve.py` today runs
  on a laptop holding the ArangoDB **root** password. In-cluster with a
  read-only role it holds strictly less privilege than it does right now, and
  needs no auth decision, because nothing connects to it from outside — it is
  reached by `kubectl port-forward`, exactly as ArangoDB already is.
  An Ingress is deliberately absent. `serve.py` has no authentication, and
  `audit-conventions` measured that this cluster has **no Traefik middleware at
  all** — no basicAuth, no forwardAuth, no ipAllowList — so "follow the existing
  Ingress convention" would publish it to the LAN unauthenticated. That remains
  a separate decision requiring auth first.
  The demotion is expressible only because `arango_db()` now honours
  `LAGMATRIX_ARANGO_USER`; it hardcoded `"root"` while `tests/conftest.py`
  already read that variable — the same helper/application drift as Q-43.
- **Outcome:** Rolled out and verified in-cluster, not merely applied:
  `/health` returns 200, `/config` reports `graphrag: true` and
  `default_as_of: 2026-09-09`, and `/movers?as_of=2026-09-09` sweeps 3,200
  symbols returning 39 movers (SNOW z=2.468, BKNG −2.2, HWM −2.134).
  The privilege claim is checked by what the pod **cannot** do: the mounted
  credential's md5 matches the read-only password and not root's, and from
  inside the pod, connecting as `lagmatrix_ro` reads 47,961 edges while
  connecting as `root` is **rejected** — it does not hold that password.
  `lagmatrix_ro` itself is denied document insert, collection create and AQL
  write, tested rather than assumed.
  Carries D-117's two deployment lessons forward: `fsGroup: 10001` so the
  secret mount is readable, and an init container for `excluded-etfs.csv`,
  which `neighbourhood()` reads and which the PVC would otherwise shadow.
- **Status:** Accepted

### D-119 — `arango_db()` says which failure it hit, answering Q-55
- **When:** 2026-09-10T13:15:00-05:00
- **Decision:** `arango_db()` records why it returned None, exposed by
  `arango_reason()`, and all four call sites render it. It still returns None
  rather than raising.
- **Why:** it swallowed every failure identically and callers printed
  "ArangoDB not reachable". During D-117 the pod could not *read* the mounted
  password — Kubernetes owns secret volumes root:root and the container runs as
  uid 10001 — and that `PermissionError` was reported as a network problem. The
  database was reachable throughout. **The message was loud and named the wrong
  cause, which is worse than a vague one, because it directs the search**: I
  changed `LAGMATRIX_ARANGO_URL` for nothing before finding the mount.
  The information already existed and was discarded — the socket probe
  distinguishes unreachable from everything else, and the block below it caught
  credential, auth and driver failures into the same `return None`.
  Three alternatives lost. Raising on non-network failures: rejected because
  `/config` calls `arango_db() is not None` and would 500, and because a laptop
  with no tunnel should still serve the page — the docstring's stated intent.
  Changing the return type to a tuple: rejected, it edits every call site to fix
  a message. Logging to stderr only: rejected, the HTTP callers return JSON to a
  browser and stderr is not where that reader looks.
- **Outcome:** 272 passed. The three failure classes now read distinctly:
  `unreachable at http://...:8529: [Errno -2] Name or service not known`;
  `credential /home/.../.lagmatrix-arango-pw unreadable: permission or path
  error`; and a connect failure naming the user and the driver's own error.
  The second is the one that cost two wrong fixes.

  **Amended 2026-09-10T17:55 — the fix reached `serve.py` and stopped there.**
  `load_vectors.py:arango()` still exited with the bare
  `sys.exit("ArangoDB not reachable")`, so the message this entry exists to
  delete survived in the one other caller — including on the nightly path. It
  now reports `arango_reason()`. Found by reading the write path before running
  it against the live database, not by a test. Grepped afterwards: no other copy
  of the bare string remains.
  Separately, per Q-59, none of this is in the running pod.
- **Status:** Accepted

### D-120 — ArangoDB's OOM kills were cache sizing, not the vector index
- **When:** 2026-09-10T18:10:00-05:00
- **Decision:** `--rocksdb.block-cache-size 768Mi` and `--cache.size 256Mi` on
  arangod. Two false claims about index rebuilding are withdrawn.
- **Why:** Q-49 recorded that `load_vectors.py` "rebuilds the whole ANN index
  every run" and that incremental indexing was the durable fix. **Both were
  wrong**, and I had written them into two committed comments.
  `add_index` on an identical definition is a **no-op**: the index id and name
  are unchanged across runs, the name's HLC tick decodes to a single training
  at 2026-09-07T22:49:25Z, 170 documents inserted after that all self-retrieve
  at rank 1 through `APPROX_NEAR_COSINE` (60/60 sampled, against a 60/60
  control from before training), and total vector-training time across 15.7h of
  uptime is **0.0133s**. The index is ~74 MiB, 2.9% of the limit. It was never
  the suspect.
  The actual cause, measured from `/_admin/metrics/v2`: arangod **detects the
  cgroup** — `effective_physical_memory` reports 2,560 MiB — and then sizes its
  caches from the **node's** 15,672 MiB anyway:

      rocksdb_block_cache_capacity  4,087 MiB = (node - 2GiB) * 0.30
      rocksdb_cache_limit           3,406 MiB = (node - 2GiB) * 0.25
      sum                           7,493 MiB = 2.93x the container limit

  Block cache usage was 1,258 MiB against **266 MiB of live SST** — 4.7x the
  entire dataset — growing with query volume and not shrinking before capacity.
  **My earlier raise from 1500Mi to 2560Mi treated a symptom**, and the pod was
  at 1,610 MiB (63%) when this was measured, not the "~525 MiB at rest" I had
  reported after the restart. It would have been killed again.
- **Outcome:** After rollout: block cache 4,087 → **768 MiB**, cache limit
  3,406 → **256 MiB**, sum 7,493 → 1,024 MiB, i.e. 2.93x the limit down to
  0.40x. Pod at 191 MiB, 0 restarts. Verified in the metric rather than
  inferred from the flag.
  Both false comments corrected in place — `10-arangodb.yaml` and
  `daily_ingest.py`'s `build()` docstring — with the mistake left visible,
  since a confident wrong sentence about a rebuild would have sent the next
  person to optimise something that does not happen.
- **Status:** Accepted

### D-121 — Default-deny egress on `lagmatrix`, answering Q-53

> **Correction, 2026-09-10T18:00 — "every allow is validated by use" was not
> evidenced by the run I cited.** In that run `bars` and `long_bars` returned
> `already current` (PVC mtimes show neither file was ever written by a job)
> and `news` fetched 0 articles with every window already covered. No node
> made an outbound 443 connection; four of the six "ok" nodes were no-ops.
> The `ipBlock 0.0.0.0/0` rule with the `except` list — the one the comment
> spends fifteen lines defending — was not exercised at all. `postmortem`
> measured it separately and it is correct; the *evidence sentence* was not.
> The policy stands; the proof I offered for it did not. See also Q-58.
- **When:** 2026-09-10T18:35:00-05:00
- **Decision:** `default-deny-egress` plus two per-workload allows. ArangoDB
  gets **no** policy, so it has zero egress.
- **Why:** the namespace reached the trading system. Measured before:
  `postgres.copytrade:5432`, `redis:6379`, `api-gateway:8082` (broker-credential
  write routes), `dashboard:3000` and `market-data:8080` all reachable from a
  pod here.
  **The question that decided whether any of this was worth doing** was whether
  k3s enforces NetworkPolicy at all — flannel historically did not, and a
  manifest that lints and does nothing would have been false comfort. It does:
  k3s runs kube-router's netpol controller **in-process in the k3s server**,
  which is why no controller pod appears in `kube-system`. Verified from
  iptables (452 `KUBE-NWPLCY`/`KUBE-POD-FW` rules, a per-pod chain already
  programmed for `lagmatrix-web` ending in REJECT) rather than by probing —
  which shows the enforcement path itself rather than one sampled outcome.
  **The trap in the except list.** kube-router compiles selectors into ipsets of
  **pod** IPs and evaluates in FORWARD/OUTPUT, i.e. *after* kube-proxy has
  DNAT'd a ClusterIP to a backend. So `except: 10.42.0.0/16` is load-bearing and
  the obvious edit — excepting only the service CIDR `10.43.0.0/16` — would leak
  the entire cluster.
  **The prerequisite that would have broken the nightly job.** The CronJob's pod
  template had no `metadata` at all, so its pods carried only `job-name` and
  `controller-uid`, regenerated every run. A default-deny selects every pod; with
  no stable label no allow could match, and the ingest would have gone from
  working to fully blocked, unattended, at 09:00 UTC. The label went on first
  and a full run went green before any policy existed.
  ArangoDB gets nothing rather than a defensive DNS grant: its netns holds
  exactly one socket (LISTEN 8529), zero conntrack flows across 15h, no
  telemetry option in `--dump-options`, and `/etc/hosts` carries its own name so
  self-resolution never reaches DNS. Granting nothing is also more informative —
  a future version that starts reaching out fails visibly instead of having been
  pre-authorised.
- **Outcome:** From the web pod: `arangodb:8529` reachable;
  `postgres.copytrade:5432` and `api-gateway.copytrade:8082` both
  **ConnectionRefusedError** — kube-router's REJECT signature, observed from
  inside a pod. A full ingest run under the policies completed with all six
  nodes ok and 24,104 edges, so every allow is validated by use rather than by
  inspection; the absence of a HuggingFace exception is validated too, since
  `vectors` still embedded 85 articles from the baked-in model.
- **Status:** Accepted

### D-122 — Embedding candidates come from a fixed floor, not the watermark (Q-56)
- **When:** 2026-09-10T19:00:00-05:00
- **Decision:** `plan_embeddings(candidate_ids, existing_keys)` in
  `lagmatrix.ingest` does a key-set anti-join; `load_vectors.py` selects
  candidates from a fixed `CORPUS_FLOOR = "2025-01-01"` and embeds whatever is
  not already in ArangoDB. `--since` becomes informational.
- **Why:** the query was bounded by the **global** corpus watermark while the
  ticker list was read fresh, so a ticker firing for the first time after the
  watermark had passed its own history was never a candidate at all. **412
  articles were missing and could never have been recovered** — their
  `created_at` is permanently behind the frontier. Forward exposure is larger:
  3,757 marginal articles for the six tickers that joined in the last month,
  41,741 across all 24 arrivals, with a new ticker every 4-5 days.
  It was invisible because a manual full re-embed on 2026-09-07 had swept it up
  — and D-115, by putting `extract_fires` in the nightly graph, removed the hand
  that was doing the sweeping.
  **The alternative that lost was a per-symbol coverage ledger**, mirroring
  `load_news.py`'s `already_covered`. Rejected: it would be a second copy of
  `article`'s own key set — a new thing that can go stale, which is this exact
  failure class — and it would not have caught the 412, whose symbols are
  already covered. Set membership against a store we own needs no ledger.
  **The observability half is why nobody saw it.** The run printed one figure,
  `47,829 articles since ...`, identical whether the candidate set was right or
  wrong. It now prints candidates / already embedded / to embed, and the
  three-way count is the function's return type rather than a print statement
  someone can drop.
- **Outcome:** 279 passed. Run against the live corpus: `48,241 candidates`,
  `47,829 already embedded`, **`412 to embed`** — the exact number measured
  independently beforehand. After it, eligible 48,241 and embedded 48,241,
  **missing 0**, with the 8 reference embeddings unchanged at cosine 1.000000.
  `--since` no longer bounds the query and now says so in its own `--help`: a
  flag that looks like it narrows the query and silently does not would be the
  same defect in a new place. It is kept because `daily_ingest.py` passes it and
  because a malformed value is still worth rejecting (D-103).
- **Status:** Accepted

### D-123 — Every script under `scripts/` is import-checked, by discovery
- **When:** 2026-09-10T19:20:00-05:00
- **Decision:** `tests/test_scripts_import.py` discovers `scripts/*.py` with a
  glob instead of naming four modules, and a guard test fails if discovery ever
  returns nothing.
- **Why:** D-116's tail. Nothing under `tests/` imports these modules and they
  are not part of the collected package, so a broken import in any of them
  leaves the suite green. That has now happened twice: deleting
  `assessor.rank_by_room` broke `serve.py` while the suite reported 111 passed
  (Q-43), and D-101's fastembed swap removed `scipy` — never a declared
  dependency, it arrived transitively via sentence-transformers — which stopped
  **both of D-95's reproduction scripts importing at all**, with the suite green
  at 248 and ruff clean. Nobody noticed until one was run by hand.
  **Discovered rather than enumerated, because the failure mode is forgetting.**
  A hardcoded list is exactly what the second incident defeated: those scripts
  had existed for weeks and had never been added to it. Coverage went from 4
  modules to 34.
  The guard on the guard exists because a glob that returns nothing would make
  every import test below pass vacuously — the same silent-skip shape as Q-43,
  one level up.
- **Outcome:** 36 tests in that file. **I logged "315 in the suite" and that
  was wrong** — it was 310 passed and 1 skipped; `postmortem` caught it. 317
  passed / 1 skipped as of D-124. Verified it catches the
  real thing rather than assumed: with `scipy` made unimportable, exactly
  `experiment_chains` and `experiment_lag_matrix` fail and the other 34 scripts
  pass. It would have caught D-101 on the day.
  It also pins that these scripts do no work at module scope — no server, no
  socket, no market data, no ArangoDB — so a script that starts doing work at
  import time fails here rather than at 3am.
- **Status:** Accepted

### D-124 — The embedding corpus covers the graph, not just `fires.csv`

- **When:** 2026-09-10T17:52:00-05:00
- **Decision:** `load_vectors.py` builds its ticker universe from
  `embedding_universe(db, fires_tickers)` — both endpoints of every
  `supplies_to` and `moves_with` edge, unioned with the alert set — instead of
  `fires.csv`'s 24 symbols alone.
- **Why:** `/movers` and `leader:` mode reach symbols the graph knows about and
  `fires.csv` does not, and those symbols had no embedded news at all, so
  GraphRAG returned nothing for them and looked like an absence of news rather
  than an absence of corpus. The alternative that lost was embedding the whole
  postgres corpus: 264,079 articles against 80,589, most of them about symbols
  no edge reaches, for retrieval nobody performs.
  The union never *replaces* the alert set, so a symbol that fires but has no
  edge yet still gets its news — the failure D-122 exists to prevent, one layer
  out.
- **Outcome:** Ran it attended rather than letting the CronJob discover it
  unattended, having measured all three variants against live postgres and
  ArangoDB first: deployed-today selects **35** to embed, HEAD alone selects the
  **same 35**, HEAD+widening selects **32,348**. That measurement corrected my
  own framing and `postmortem`'s: D-122 changes nothing about volume, because
  `fires.csv`'s 24 tickers were already fully embedded. The entire 32,348 is the
  widening.
  Embedded 32,348; `article` 48,241 → **80,589**, exactly the predicted target;
  peak RSS 1,284 MiB. Coverage 3,899 → **5,516** symbols; **1,617** symbols went
  from zero embedded articles to present; **315** previously-thin symbols crossed
  10+. BKR 9 → 289, VEEV 7 → 245.
  Retrieval verified end-to-end rather than inferred from counts, because
  coverage is not retrieval: both return relevant, correctly-attributed hits at
  74ms. **0 lookahead violations** across 12 symbol/`as_of` pairs after a 67%
  corpus growth.
  I expected to find that the `LET score = APPROX_NEAR_COSINE(...)` / `FILTER`
  shape post-filters, which would starve thin symbols *worse* as the corpus grew.
  It does not: the optimiser folds the predicate into `EnumerateNearVectorNode`
  and pre-filters inside the ANN scan. There is no `FilterNode` in the plan at
  all, which is what made me look — and for a moment looked like the
  point-in-time guard had been silently dropped.
  Still true and worth stating: **4,531 of 5,516 symbols remain thin (<10
  articles)**, because the widening admitted 1,617 new symbols mostly at low
  counts. Coverage broadened; it did not deepen.
  **Verified in-cluster 2026-09-10T18:07** on image `faa1076`, attended: the job
  succeeded at **899 MiB peak of a 3072 MiB limit**, and `node_vectors` reported
  `80,589 already embedded / 84 to embed -> indexed: 80,673`. The delta is small,
  which is the whole point of having run the 32,348 attended first.
  `postmortem` predicted the job would see "35 + a day or two of alert-universe
  news", not 35, because `node_vectors` runs after `node_news`. It saw **84**.
  Logging 35 as the expected value would have sent someone hunting a bug.

  **Did it help retrieval? Measured 2026-09-11 — yes, and by less than the
  coverage numbers imply.** Coverage is not retrieval, and the three-symbol
  "it returns hits" demo above proves nothing on its own: it shows the query
  runs, not that the answer improved.
  Counterfactual, since the before-state could not be re-run once the vectors
  were written: the pre-backfill corpus is exactly what the **old fires-only
  candidate query** selects, so it was reconstructed from postgres (48,306 keys
  against the true 48,241 — last night's run added a few, which makes every
  figure below *conservative*). Each symbol's own article pool was then ranked
  by brute-force cosine rather than through the ANN, so the two conditions
  differ only in which documents exist — no index difference, no approximation.
  150 graph-reached, previously-thin symbols, 5 query topics x 2 `as_of` dates:

  | | `as_of` 2025-09-01 | `as_of` 2026-06-01 |
  |---|---|---|
  | returned **nothing** | 105 -> **66** | 75 -> **36** |
  | had >= 5 candidates | 1 -> **19** | 15 -> **44** |
  | mean best-match cosine | 0.13-0.20 -> 0.16-0.26 | 0.14-0.22 -> 0.18-0.28 |

  Symbols retrieving *nothing* roughly halved at both dates, consistently across
  query topics rather than on the single probe run first.
  **Three caveats, recorded so the numbers are not over-read.** (1) "Never worse"
  is structural, not a quality result: the after-set is a superset, so a best
  match can only rise — it is not evidence. (2) **Absolute quality stays modest.**
  A mean best-match cosine of 0.28 on MiniLM is a weak semantic match, and
  `regulatory investigation lawsuit settlement` barely moved (0.134 -> 0.161),
  which reads as noise in both conditions: the corpus now *has* articles for
  these symbols, it does not have articles on that topic for them. (3) **36 of
  150 still return nothing** at the recent date, 66 at the older one.
  So D-124 did the thing it was built for — it halved the "GraphRAG returns
  nothing, which looks like an absence of news rather than an absence of corpus"
  failure — and it did not turn thin symbols into well-covered ones. Whether
  0.28 is good enough to change a downstream decision is a separate question and
  is **not** claimed here.
- **Status:** Accepted

### D-125 — The ingest's memory limit is 3Gi, from the write that had never run

- **When:** 2026-09-10T17:58:00-05:00
- **Decision:** `22-lagmatrix-ingest-cron.yaml` raises `limits.memory` 2Gi →
  **3Gi** and deliberately leaves `requests.memory` at **512Mi**.
- **Why:** `fetch_daily_bars.write()` — `to_parquet(compression="zstd",
  compression_level=6)` over 4.7M rows — is guarded by `if len(new)` and had
  therefore **never once executed in-cluster**. Every memory figure this
  deployment was sized from structurally excluded it, including the 799 MiB I
  cited. Raised by `postmortem`, which estimated 1.2–1.6 GiB.
  Request and limit answer *different* failures and want opposite levers. The
  kubelet ranks eviction by usage-above-request, so a Burstable pod asking 512Mi
  and using ~1.3Gi is the first thing evicted when the **node** runs short — and
  this node runs real-money trading with ~2.2 GiB available. That is the outcome
  we want: the ingest dies, copytrade does not. The limit is what stops the
  **cgroup** killing a job that would have finished, which matters because
  `backoffLimit: 0` means an OOMKill loses the whole run with no retry.
  `compression_level` was rejected as the lever: zstd 6 vs 1 trades CPU, not peak
  memory. Writing uncompressed was rejected as actively worse — it removes a
  ~30 MB compressor buffer and inflates the in-memory output from ~30 MB to
  ~150–200 MB, raising peak.
- **Outcome:** Measured, not estimated: the full path — fetch, non-empty
  `merge_bars` over 4.7M rows, zstd level-6 write — peaks at **965 MiB**
  (988,184 KB) in 5.5s. Read-plus-write in isolation is 539 MiB. `postmortem`'s
  range was ~35% high.
  **My first two attempts to measure it both returned `+0 new rows`** and would
  have been reported as measurements of a path that never executed — the
  identical structural gap I was deliberately hunting, reproduced twice while
  hunting it. Alpaca publishes no daily bar for the current day even after the
  close, so `watermark+1 -> today` fetches nothing. The real path required
  trimming the last day (2,183 rows) out of a scratchpad copy to force a genuine
  fetch and a non-empty merge; it restored the file to exactly 4,736,621 rows and
  incidentally exercised the split path (`APH`).
  It runs as a **subprocess** of `daily_ingest.py` (`_run([sys.executable, ...])`),
  charged to the same cgroup while the parent holds pandas, pyarrow and
  langgraph — so 965 MiB is the child's share, not the cgroup total. Live
  CronJob confirms `{"limits":{"memory":"3Gi"},"requests":{"memory":"512Mi"}}`.

  **The attended run did NOT exercise this path, and that was predicted, not
  discovered.** `node_long_bars` skips when `last >= today - 1`; the PVC holds
  through 2026-09-09 and it was 09-10, so the log printed `long_bars: already
  current through 2026-09-09` exactly as forecast. **Tomorrow's 09:00 UTC run is
  the first execution of the zstd write in-cluster**, because only then is
  `today - 1` ahead of the file. The write risk is therefore covered by the
  965 MiB measurement and the 3Gi headroom — not by tonight's green result, and
  a green result should not be read as covering it.

  **Ran 2026-09-11T09:00Z, and the write executed.** `wrote
  data/bars-10y.parquet: 4,738,804 rows (+2,183 new rows, 2,183 fetched)` — the
  first in-cluster execution of the zstd path, in a job that succeeded in 3m43s
  (against 1m46s for the attended run that skipped it). Everything downstream
  ran: 24,145 edges, 18 new embeddings, 80,691 indexed.

  **Peak was 919 MiB of the 3072 MiB limit**, from Prometheus
  (`container_memory_working_set_bytes`, which is the metric the cgroup OOM
  killer acts on, so it is the right one for this question).

  **So the raise was not needed, and that should be said plainly rather than
  left to look vindicated.** 919 MiB fits inside the original 2Gi with room to
  spare, and the write added only ~20 MiB over the attended run's 899 MiB —
  nowhere near the 400–500 MB `postmortem` estimated on top, nor my own 965 MiB
  for the subprocess in isolation. The parent's and child's peaks evidently do
  not coincide the way both of us assumed, and max-RSS (what I measured locally)
  is not working-set (what the cgroup enforces).
  The decision stands anyway: limits are not reserved, so 3Gi costs nothing, and
  2.2x headroom over a corpus that grows daily is thin. But it was insurance
  bought on an estimate that the measurement has now superseded, and D-125's
  reasoning was better than its arithmetic.
- **Status:** Accepted

### D-126 — The ingest refuses to start until its egress policy is observably enforced

- **When:** 2026-09-11T05:11:00-05:00
- **Decision:** An `await-netpol` init container runs before everything else in
  the ingest pod. It proceeds only when a **denied** target is blocked **and**
  an **allowed** target is reachable, and exits non-zero after 30s otherwise.
- **Why:** Q-58. kube-router programs the pod's `KUBE-POD-FW-*` chain from a
  pod-add informer event, which needs `status.podIP` and so cannot happen until
  after CNI ADD. The netns is wired — the container can send packets — for 1–3s
  before the NetworkPolicy reaches it. Nothing in the NetworkPolicy API
  expresses "fail closed until programmed", and `--iptables-sync-period`
  governs the periodic full sync, not the pod-add path.
  Two alternatives lost. **A CNI migration** (Calico programs the drop chain
  during ADD; Cilium gives a new endpoint a policy-subject init identity) would
  genuinely remove the window, and means replacing flannel+kube-router on the
  node that runs real-money trading to fix three seconds on a batch job.
  Disproportionate. **A `sleep`** was rejected because it is the same wait with
  no outcome: it cannot tell an enforced policy from a broken one, and it would
  still pass if a k3s upgrade broke enforcement outright — which is the durable
  worry behind D-121.
  This is a precondition with an explicit outcome, and with `backoffLimit: 0`
  the job dies loudly rather than running unattended with postgres credentials
  inside an unenforced namespace.
- **Outcome:** **Two details are load-bearing and both were verified, not
  assumed.**
  *Probe a denied target.* During the window everything is open, so "arangodb
  reachable" holds in both states and proves nothing. Only a target the
  allow-list excepts distinguishes them — `:6443` on the LAN, covered by
  `except: 192.168.10.0/24` and not port 443. Confirmed it discriminates: an
  unrestricted pod in `default` reaches it (`REACHABLE`), a pod carrying
  `app: lagmatrix-ingest` does not (`blocked`). A probe target that is merely
  down would have made the whole guard vacuous.
  *Require an allowed target to be up as well.* `postmortem` proposed waiting
  only for the denied target to close. That passes when the network is simply
  broken — DNS down, CNI wedged, the API server offline — because the
  connection fails for the wrong reason while nothing is enforced. Tested
  rather than argued: a pod in this namespace with the wrong `app` label is
  selected only by `default-deny-egress`, so all egress is blocked, and it
  reports **`denied_blocked=True, allowed_up=False`** and fails after 30s. A
  denied-only probe would have passed it. That single test is why the second
  condition exists.
  Normal path measured at **0.5s**.
  **A correction to my own measurement of the window.** My first probe printed
  timestamps (`0.0s OPEN`, `0.2s OPEN`, …) that were computed as `i * 0.25`,
  not read from a clock. The loop had no `sleep`, and a successful connect
  returns in about a millisecond, so all 120 "samples" fit inside roughly two
  seconds of wall clock. The numbers were arithmetic presented as observation —
  the same defect this log exists to catch. Re-measured with real clock reads,
  the window is real and lands 1–3s after pod start; `postmortem`'s 0.1–3.1s
  bound stands and I could not narrow it.
- **Status:** Accepted

### D-127 — The unauthenticated redis is an accepted risk, not an open task

- **When:** 2026-09-11T05:30:00-05:00
- **Decision:** Q-60 is closed as **accepted and declined**. The trading
  system's redis keeps no password. No change is made to `copytrade`.
- **Why:** The owner's call, made with the measurements in front of them, and
  the premise is largely correct: redis is `ClusterIP` with no `nodePort`, no
  LoadBalancer and no host-level `:6379` listener, so nothing reaches it from
  outside the cluster. Every traefik ingress is `.local` or `nip.io` bound to
  the LAN. Against that, a password on a live real-money service costs a
  restart with real blast radius, to defend a service nothing off-LAN can
  address.
  The alternative that lost was setting `requirepass`. The argument for it was
  never an internet port-scan — it was that the realistic path is a workload in
  this cluster misbehaving rather than an intruder arriving: `open-webui`, two
  Discord mirrors processing external messages, and **this project's own ingest,
  which pulls arbitrary news text off the internet and embeds it**. Any of those,
  or any future workload in a namespace with no egress policy, reaches those 15
  keys with no credential. That argument was made once, weighed, and rejected.
  Not re-litigated here.
- **Outcome:** Recorded so it reads as a decision rather than a forgotten
  to-do. **One fact is kept because it is the thing that would change the
  answer:** `cloudflared` runs with `TUNNEL_TOKEN` and **no config file and no
  configmap** — a remotely-managed tunnel whose routing table lives in the
  Cloudflare dashboard, not in this cluster and not in any repo. "Nothing
  external can reach the cluster" therefore cannot be verified from inside it,
  and can change without any commit or manifest changing. That is not an
  argument against this decision; it is the condition under which the decision
  should be revisited.
  **Revisit if:** the tunnel begins routing to `copytrade` or `apps`, a
  workload running untrusted code is added to a namespace with no egress
  policy, or redis starts holding anything whose corruption would cost money
  rather than a cache miss.
- **Status:** Accepted

### D-128 — "No company moved with it" no longer covers for a missing session

- **When:** 2026-09-11T15:10:00-05:00
- **Decision:** `followers()` and `network()` call `session_available()` before
  `comovement_edges()` and return `{"error": reason}` when the requested session
  cannot be answered, instead of an empty-but-successful payload.
- **Why:** Reported by the user: picking some recent dates showed *"No company
  has moved with X reliably enough to clear |r| >= 0.5 ... some names really do
  move on their own."* That sentence is a claim about the market, and it was
  being rendered in three cases where the truth is "there is no data for that
  date". `comovement_edges` returns `[]` when `as_of` is absent from the frame
  or when fewer than `trail` sessions precede it — a deliberate contract — and
  both endpoints turned that silence into a finding.
  Measured on the live service, all three return 0 followers with `error=None`
  for DELL, which genuinely has 4 followers on a real session:
  **2026-09-12** (a Saturday, selectable from the date box), **2026-09-11**
  (newer than the file), and **2016-09-20** (inside the first 250 sessions).
  The second is the everyday case and the reason the report said *recent* dates:
  **`bars-10y.parquet` can never contain today.** Alpaca publishes no daily bar
  for the current session even after the close — measured in D-125, where two
  separate attempts to exercise the bars write both returned `+0 new rows`. So
  the newest session is always yesterday, and picking today always looked like
  nothing moved with anything.
  `session_available()` already existed for exactly this, returns `(ok, reason)`,
  and was used by `daily_ingest.py:195` but by neither endpoint. The alternative
  that lost was a new error path in `serve.py`: it would have duplicated the
  message and let the two drift, which is why a test pins that the string comes
  from the helper rather than a local copy.
- **Outcome:** Two lines per endpoint. **No UI change was needed** —
  `serve_index.html:1093` already checked `d.error` first and returned before
  touching `d.followers`, so the seam existed and simply was not used by these
  two callers.
  Red wrote 5 tests and I verified the failure myself before green ran: 4 failed,
  1 passed. The pass is the positive control, which exists because an
  implementation that returned an error unconditionally would otherwise satisfy
  every other test. Suite 317 -> **322 passed, 1 skipped**; ruff clean.
  Verified against real data rather than the fixture alone: 2026-09-08 and
  2026-09-09 still return 4 followers, while the three bad dates now report
  `2026-09-12 not found in data (last session available: 2026-09-09)` and
  `only 10 sessions precede 2016-09-20, need 250`.
  **The user's original question was answered separately and the answer was "it
  was real".** On 2026-09-08 the data was complete — 250 sessions, 2,183 symbols
  — and RARE (best 0.339 with DNLI), TARS (0.386, IONS), TDS (0.373, DUKU),
  LULU (0.436, ABNB) and OXM (0.382, OPEN) genuinely had no peer clearing 0.5.
  LULU is the near miss: 13 names appear at 0.4. So the message was true that
  day and could not have told them so.
- **Status:** Accepted

### D-129 — The correlation threshold defaults to 0.4, and the control now works

- **When:** 2026-09-11T17:05:00-05:00
- **Decision:** `min_abs_corr` defaults to **0.4** rather than 0.5, and
  `/followers` honours the query parameter it had been ignoring.
- **Why:** The owner asked for 0.4 after D-128's investigation showed LULU's
  best match (ABNB, 0.436) sitting just under the old cutoff — a real
  co-movement the page could not show. Answering that request surfaced a
  second, worse defect: **`/followers` never parsed `min_abs_corr` at all.**
  Measured on the live pod, every value returned the same result, including a
  deliberately absurd one:

  ```
  /followers?symbol=LULU&as_of=2026-09-08                  -> min_abs_corr=0.5
  /followers?symbol=LULU&as_of=2026-09-08&min_abs_corr=0.4 -> min_abs_corr=0.5
  /followers?symbol=LULU&as_of=2026-09-08&min_abs_corr=0.3 -> min_abs_corr=0.5
  /followers?symbol=LULU&as_of=2026-09-08&min_abs_corr=0.9 -> min_abs_corr=0.5
  ```

  The UI compounded it. `serve_index.html:1097` overwrote the server's echoed
  value with the input box's value *after* the fetch, so the page labelled every
  result with a threshold that had never been applied. The displayed threshold
  and the applied threshold were independent quantities that happened to agree
  only at 0.5. **Changing the default alone would have left the control inert**,
  which is why this is one decision and not two.
  `/network` had parsed it correctly all along, so the asymmetry was between two
  sibling endpoints — the same shape as D-104 and D-108.
- **Outcome:** Defaults changed in four places that can drift independently
  (both signatures, both handler fallbacks), `/followers` now uses
  `parse_bounded(..., 0.1, 1.0)` and 400s on an out-of-range value like
  `/network` already did, the page sends the box's value, and the post-fetch
  overwrite is deleted so the label reflects what was actually filtered.
  `CoMovementFollowers(..., min_abs_corr=0.6)` at `serve.py:206` is a different
  feature and was deliberately left alone.
  Red wrote 7 tests, 6 failing, verified before green ran. **Red's first draft of
  the pass-through test used `0.5` as its explicit value and passed against the
  broken handler**, because 0.5 was the hardcoded fallback — it caught this
  itself and switched to `0.3`, outside both the old and new defaults, so no
  coincidence with either can satisfy it. That is the whole reason the test is
  worth having.
  Suite 322 -> **329 passed, 1 skipped**; ruff clean.
  Verified on real data: **LULU 0 -> 13 followers, top ABNB at +0.436**; DELL
  4 -> 11, top HPE at +0.648. RARE, TARS, TDS and OXM stay at 0, consistent with
  their measured bests of 0.339-0.386 — the threshold moved, the measurements
  did not.
- **Status:** Accepted

### D-130 — `Candidate` carries the claiming leader's shock magnitude

- **When:** 2026-09-11T18:55:00-05:00
- **Decision:** `Candidate` gains `origin_sigma: float | None = None`, populated
  by `MarketScan.candidates()` with the claiming leader's **signed** z.
  `ExternalSignals` leaves it `None`.
- **Why:** Amends PLAN-2026-09-11-quant-daytrade-perspectives mid-execution.
  `red-p3-llm` found that `cap_for_llm`'s sigma-ordering branch was
  unreachable — `Candidate` had no such field and, being pydantic, rejects one
  on attribute assignment. Checking the consequence made it worse than dead
  code: `MarketScan.candidates()` sorts leaders by shock magnitude at
  `candidates.py:121`, has `z` in scope at `:123`, and then returns
  `sorted(claims.values(), key=lambda c: c.symbol)` at `:136` — **the shock
  ordering is computed and discarded, and the output is alphabetical.**
  With the plan's 20-candidate cap against D-92's mean of ~121 candidates per
  date, that meant paying to analyse the alphabetically-first 20 every day,
  the same names each time, skipping the largest movers. A stable, invisible
  selection bias, which is this project's signature failure.
  Two alternatives lost. **Accepting alphabetical capping** and documenting the
  bias: cheapest, and wrong for exactly the reason above. **Dropping the cap**:
  removes the bias by removing the choice, and unbounds a cost the cap existed
  to bound.
  The signed z is stored rather than `abs(z)` because `direction` is already
  derived from that z's sign one line later, so the sign was already in the
  model implicitly; storing the raw value preserves the measurement and lets
  `cap_for_llm` apply `abs()` itself.
- **Outcome:** Two lines of implementation. The interesting part is what the
  existing tests did.
  **`test_candidates_are_plain_candidates_with_no_forward_looking_field` fired,
  correctly** — it pins `Candidate`'s exact field set specifically to catch an
  added field, and its docstring already cited the `extra="ignore"` trap.
  `origin_sigma` was admitted deliberately and the justification written into
  that docstring rather than the set being quietly widened: it is a realised
  move over the window ending at `as_of`, not an expectation or a score, and
  `direction` already encoded its sign. A field naming something that has not
  happened yet still falsifies the guard.
  Two `test_market_scan.py` equality assertions also went stale. Both were
  repaired by reading the expected z back from `shocked_leaders()` — a
  different code path from `candidates()`, so it is not circular — which
  **strengthened the dedup test**: `SHARED` must now carry the winning
  leader's z, so a dedup that kept the wrong leader fails on the value as well
  as the name.
  Surfaced **Q-61**: `Candidate` has an empty `model_config`, so pydantic
  silently drops unknown kwargs. Verified — `Candidate(..., origin_sigma=5.0)`
  constructed cleanly *before* this change and discarded the value. Adding the
  kwarg to `MarketScan` without the model field would therefore have been a
  silent no-op that left the bias in place with no diagnostic at all.
  340 passed, 1 skipped; ruff clean.
- **Status:** Accepted

## Open Questions

| ID | Question | Blocks | Notes |
|----|----------|--------|-------|
| Q-60 | ~~The trading system's redis requires no password, and any pod in the cluster can reach it~~ **CLOSED by D-127 — accepted risk, declined** | what the 3s NetworkPolicy window (Q-58) actually exposes | Measured 2026-09-10. `redis-cli CONFIG GET requirepass` returns an **empty value** — no password is set — and `redis-cli PING` answers unauthenticated. From a busybox pod in the **`default`** namespace, holding no credentials, raw `nc 10.43.102.122 6379` with `PING` returns `+PONG`. So the control in front of it is network reachability alone. Scope, checked rather than assumed: `type=ClusterIP`, no `nodePort`, no LoadBalancer, and no host-level `:6379` listener on the node — it is reachable from **inside the cluster only**, not the internet. `DBSIZE` is 15, all keys carrying TTLs; contents deliberately not read. Found by following `postmortem`'s reframing of Q-58: the right question was not "how do I close a 3-second window" but "what is protected *only* by the NetworkPolicy". Of Q-53's five reachable targets, postgres and ArangoDB have their own authn and api-gateway's credential routes need credentials, leaving `redis:6379`, `dashboard:3000`, `market-data:8080` and `orchestrator:8080` — and redis is the one with no auth at all. **This is the finding, not the window:** `lagmatrix`'s default-deny now blocks it except during Q-58's 0.1–3.1s gap, but every other namespace in the cluster is unrestricted, so the window is not the exposure's main cause. The proportionate fix is a redis password, not a CNI migration. **Closed 2026-09-11 by D-127: accepted and declined.** Weighed against the verified exposure boundary (ClusterIP, no nodePort, no host listener, LAN-only ingresses) and the cost of restarting a live real-money service. D-127 records the condition that would change the answer — `cloudflared` is a remotely-managed tunnel whose routing is configured outside this cluster, so the "nothing gets in" premise is not verifiable from within it. |
| Q-61 | `Candidate` silently discards unknown constructor kwargs, so a forgotten or mistyped field is a no-op rather than an error | any change that adds a field to a domain model | Surfaced 2026-09-11 while executing PLAN-2026-09-11-quant-daytrade-perspectives. `Candidate` is a plain pydantic model with an empty `model_config`, so it inherits `extra='ignore'`. Verified directly: `Candidate(symbol='A', ..., origin_sigma=5.0)` constructs successfully today and `hasattr(c, 'origin_sigma')` is `False` — the value is dropped with no error and no warning. The immediate consequence was concrete: adding `origin_sigma=` to `MarketScan.candidates()` **without** also adding the model field would have run clean, produced no diagnostic, and left candidates ordered alphabetically — i.e. silently failed to fix the very bias it was written to remove. Caught by a red test asserting the actual per-leader signed values rather than merely "not None". The narrow case is closed by D-130, but the general hazard is not: every `Candidate` construction site in `src/` and `tests/` is exposed to the same silent drop, and the same is true of any other domain model with a default `model_config`. **Not fixed:** `extra='forbid'` would change validation behaviour for every construction in the codebase and was deliberately kept out of an in-flight plan execution (CLAUDE.md §3). Worth deciding on its own, across `domain/models.py` as a whole rather than one model. |
| Q-58 | ~~NetworkPolicy is unenforced for the first ~1–3 seconds of a pod's life~~ **ANSWERED by D-126** — the window is real and cannot be closed without changing CNI; the ingest now refuses to start until enforcement is observable | D-121's isolation claim, for Jobs specifically | Measured by `postmortem` 2026-09-10 with a looping probe and a control pod (no `app` label, so only `default-deny-egress` selects it): `t+0.1s alpaca=OPEN arango=OPEN pg-copytrade=OPEN`, `t+3.1s` all three `ConnectionRefusedError`. kube-router programs the pod's `KUBE-POD-FW-*` chain on the pod-add event; until it does, the pod has no chain and the namespace default-deny does not reach it. The ingest-labelled pod shows the same window on its denied target. **Irrelevant for a Deployment; the ingest is a Job — the workload class where a fast-failing container is most likely to open a socket inside the window.** We are not exposed today only because the pod spends those seconds importing pandas and langgraph before it opens anything: that is timing, not a boundary. Worth recording that `postmortem`'s *first* probe ran entirely inside the window and reported that nothing was enforced at all — a false negative in its own method, caught only because it built a control rather than believing the result. **Fixed by D-126** with an `await-netpol` init container rather than a `sleep`, after reproducing the window here. Worth keeping: `postmortem`'s *first* probe ran entirely inside the window and reported that nothing was enforced at all — a false negative in its own method, caught only because it built a control rather than believing the result. My own first re-measurement then printed computed timestamps as if they were observations. The window survived two bad measurements by two different parties before either of us measured it properly. |
| Q-59 | ~~The deployed image is seven commits stale, and nothing was ever going to build a newer one~~ **ANSWERED 2026-09-10 by the cutover to `faa1076`** | every claim in D-119, D-120, D-122 and D-123 about what *runs* | The CronJob and `lagmatrix-web` both run `ghcr.io/ridopark/lag-equity-matrix-ai:66b11a5`, which is the commit **before** D-119. Raised by `postmortem` as "nothing newer was ever built"; the root cause is mine to state: `.github/workflows/build-images.yml` triggers only on `push: branches: [main]`, and all seven commits are on `graphrag-showcase-and-scan` (PR #2, unmerged). **No build failed — none was ever triggered.** `imagePullPolicy: IfNotPresent` plus two hand-imported tags in `k3s ctr images ls` confirms 66b11a5 arrived by hand, not by the pipeline. `postmortem`'s "nothing newer was deployed" was too broad and it withdrew it: the **yaml-carried** work *is* live (three netpols present, arango limit 2560Mi, `0 9 * * 2-6 tz=UTC`, and now D-125's 3Gi), because `kubectl apply` needs no image. Only three of the seven commits carry runtime code: 90a1c17 (`load_vectors.py`, `ingest.py`), a5241ac (`serve.py`, `daily_ingest.py`), ee31cf9 (`daily_ingest.py` — verified comment-only, zero runtime risk). Consequence while it stands: D-119's distinct failure messages and D-122's fixed-floor candidates do not exist in the pod, so an ArangoDB failure tomorrow still reads "ArangoDB not reachable" — the exact message D-119 exists to delete. **Closed 2026-09-10T18:07:** built `faa1076` from HEAD, verified the image *contains* the four fixes before shipping rather than trusting the build, imported it to the node and cut both the CronJob and `lagmatrix-web` over. Attended run succeeded at 899 MiB of a 3072 MiB limit. **The structural cause is not closed and became Q-57's recurrence:** `main` is now behind the code that is actually running, so a rebuild from `main` still yields an image without D-122/D-124. Merging the branch is what fixes that, not the cutover. |
| Q-53 | ~~The `lagmatrix` namespace reaches the trading system's postgres, redis and api-gateway~~ **ANSWERED by D-121** | the isolation claim the deploy work rests on | Measured 2026-09-10 from a pod in `lagmatrix` (busybox, `nc -z`, with a control target — the first attempt reported everything blocked because the arangodb image has no bash and the command never ran, exit 127): **REACHABLE** postgres:5432, redis:6379, api-gateway:8082, dashboard:3000, market-data:8080. **blocked** exec-alpaca-live:8080 (its own ingress NetworkPolicy, working). `orchestrator:8080` blocked despite having an endpoint and no visible policy — unexplained, not claimed as protection. `audit:8081` has 0 endpoints so its result proves nothing. Two NetworkPolicies exist cluster-wide, both in `copytrade`, both ingress-only, both protecting the exec pods. **I asserted the opposite of this twice**: first that a namespace prevented reaching the trading workloads, then that `kubectl get networkpolicy -A` returns nothing — a command I had not run. Both corrections are left in `10-arangodb.yaml` rather than the sentences deleted. The reachable postgres is the same one D-103's injection would have reached. Unanswered: a default-deny egress policy in `lagmatrix` is the fix, but its allow-list depends on Q-54 first. |
| Q-54 | ~~The ingest CronJob cannot run **any** node in-cluster~~ **CODE HALF ANSWERED 2026-09-10** — `src/lagmatrix/pg.py` plus refactors of `extract_fires.py`, `load_vectors.py` and `load_news.py` (c37498e, a9468a8, 7f71270, e618202). No executable line in the ingest path shells out any more, verified by AST rather than grep. A scoped `lagmatrix_ingest` role replaces `kubectl exec` as the `temporal` superuser: read/write on our seven tables, read-only on `public.audit_log`, denied on every other table, on DELETE and on DDL — each denial tested, not assumed. Every SQL statement in those scripts was built by f-string interpolation and is now parameterised, closing the last of D-103's pattern here. Regression-checked by a full real ingest: identical output to the previous night's kubectl run (230,317 articles, 85 embeddings, 24,104 edges), stores unchanged, reference embeddings bit-identical. **Still open:** the PVC has never been seeded with `bars-10y.parquet`, and nothing has applied the CronJob. | `scripts/load_vectors.py:32` shells out to `kubectl -n copytrade exec -i postgres-0 -- psql`, and `:41` to `kubectl -n lagmatrix exec -i deploy/arangodb`. That works from a laptop with a kubeconfig and cannot work from the pod in `22-lagmatrix-ingest-cron.yaml`, which ships no kubectl, sets `automountServiceAccountToken: false`, and mounts a read-only root filesystem. **I first reported this as "4 of 5 nodes work" and that was wrong** — measured by running the image, all five fail. `bars` reads `data/fires.csv` (private, in neither repo nor image); `long_bars` exits "seed it with fetch_history.py first" on an empty PVC; `news` needs fires.csv and the same kubectl path into postgres; `comovement` finds no bars-10y.parquet and reports it via D-110. Two independent problems, both one-time: the PVC has never been seeded with fires.csv/bars.parquet/bars-10y.parquet (the ingest extends those files, it does not create them, and fires.csv is private so seeding is an operator step CI cannot do), and two scripts reach postgres by shelling out to kubectl. Three options, none chosen: give `load_vectors.py` a direct postgres connection (mostly a connection string and a Secret, and it removes cluster credentials from a batch job that should not hold them); or grant the CronJob exec rights into `copytrade` and ship kubectl (which hands a nightly batch job the ability to exec into the trading namespace — the wrong direction); or keep vectors on an operator machine. This also gates Q-53's egress allow-list, since option 1 needs postgres reachable and option 3 does not. Found by checking what the scripts need at runtime rather than by re-reading the manifest. |
| Q-52 | A stale `.ruff_cache` reported a lint error as clean, locally, for an unknown period | trust in every local `ruff check` result | CI failed PR #1 on `I001` in `tests/test_edgar_relations.py`, a file nobody had touched. Locally `ruff check` said **All checks passed**; `ruff check --no-cache` on the same bytes found the error. Same ruff (0.16.6), same config, same file content in HEAD and the working tree — the cache alone differed. Likely cause: `[tool.ruff] src = ["src", "tests"]` makes isort classify `lagmatrix` as first-party, and the cached verdict predates the environment change that made that resolvable (the fastembed re-sync reinstalled the project); ruff's cache key did not capture it. **Unverified**, and worth verifying before relying on any local lint result again. The consequence is the part that matters: every "ruff clean" reported in this session's commit messages was taken from the cached path and was not trustworthy. CI is unaffected — a fresh runner has no cache, which is why it caught this and local runs did not. Options: run `--no-cache` locally before pushing, drop the cache in a pre-commit hook, or treat CI as the only authority on lint. Not decided. |
| Q-50 | ~~Nothing schedules `daily_ingest.py`; there is no "tomorrow's run"~~ **ANSWERED by D-117** | the entire point of a *daily* ingest | Checked 2026-09-10: no crontab entry, no systemd timer, and `kubectl -n lagmatrix get cronjobs` returns **No resources found**. The pipeline is correct and verified end to end, but it executes only when a human types the command. Every "tomorrow's run will now fail loudly instead of silently" claim in D-110/D-111 is conditional on something invoking it, and today nothing does. Three options, none chosen: a local cron on the dev box (unreliable — it is WSL, not always running), a systemd timer (same caveat), or the k8s CronJob that `PLAN-2026-09-09-homelab-deploy.md` PHASE-5 specifies, which is the real answer and is blocked behind containerisation and D-101's unimplemented torch swap. Worth deciding before treating the ingest as operational. **Closed 2026-09-10:** the k8s CronJob is applied and `0 9 * * 2-6` with `timeZone: UTC` (D-115 records why the timezone was not optional — unset, it ran at 14:00 UTC, 30 minutes after the US open, where `fetch_bars.py` would have written a partial current-day bar). It has now run unattended on schedule and attended on demand, succeeding both times. |
| Q-56 | ~~**ANSWERED by D-122**~~ `load_vectors.py` bounds articles by a **global** watermark but reads the **current** ticker list, so a newly-fired ticker never gets its history embedded | retrieval quality for any ticker that enters the alert set | `load_vectors.py:100` uses `WHERE a.created_at >= %s` where `since` is `MAX(article.date)` across the whole corpus (`daily_ingest.py:82`), while `:84` reads today's `fires.csv`. A ticker that first fires next week therefore enters the embedding universe with `--since` already at the corpus frontier, and its **historical** articles are never embedded — so retrieval for it at a past `as_of` returns thin or empty results. Wrong in the safe direction, and silent, which is this project's signature failure. `load_news.py` gets the same problem right with a per-symbol `already_covered` window ledger (`:203`); the asymmetry is that one script tracks coverage per symbol and the other assumes a single global frontier. Dormant while `fires.csv` was a static artefact; **D-115 made it live** by putting `extract_fires` in the nightly graph. Found by `quant-fires` while answering a different question. Not fixed: the fix is a per-symbol coverage ledger for embeddings, which is its own cycle. |
| Q-57 | `:latest` on ghcr means "whatever was pushed last", and main cannot run in-cluster | anyone redeploying from `:latest` | CI published `:latest` from main at 08:37; a manual push overwrote it at 17:00 with the correct code, so it happens to be right **by push ordering alone**. main still shells out to `kubectl` in `load_news.py` and has no `extract_fires` node, so an image built from it cannot run any node in a pod (Q-54). The deployed pods are pinned to a SHA and are unaffected, which is precisely why sec-deploy recommended pinning. Resolved by merging the branch; recorded because the hazard is structural, not a one-off — any future CI run on a stale main silently repoints `:latest` at code that cannot run. |
| Q-55 | ~~`arango_db()` reports "not reachable" for any failure~~ **ANSWERED by D-119** | diagnosing an unattended 3am failure | `scripts/serve.py:105` is `except Exception: return None`, and callers render that as "ArangoDB not reachable". During D-117's deployment a `PermissionError` on the mounted password file was reported that way, and the database was reachable the whole time — the message sent the search to the URL, which was changed for nothing. The socket probe above it already distinguishes unreachable from everything else, so the information exists and is discarded. Returning None rather than raising is deliberate and should stay (the correlation half of the pipeline needs no database), but the *reason* should survive. Not fixed: it touches a function every endpoint calls, and deserves its own red/green cycle rather than being bundled into a deployment commit. |
| Q-51 | The two bars files cover different universes: 3,201 vs 2,183 symbols | which symbols co-movement can ever see | `data/bars.parquet` carries 3,201 symbols (a liquidity screen, median $vol >= $10M or a candidate, refreshed every run) while `data/bars-10y.parquet` carries 2,183 (pinned when it was first built). So roughly a thousand symbols appear in the wide file — and in `/movers`, which swept 3,201 — that co-movement can never return as a follower, because they have no long history stored. Whether the long file's universe should be refreshed, and what that costs against D-95's replication being measured on the fixed 2,183, is undecided. Raised by `plan-ingest`, which declined to guess rather than rationalising it. Related to the universe question already open under `PLAN-2026-09-09-daily-ingest.md` PHASE-7. |
| Q-49 | **ANSWERED by D-120** — the cause was cache sizing from node RAM, not the index rebuild I assumed; the limit raise below was a symptom fix. ~~ArangoDB is being OOM-killed~~ **PARTLY ANSWERED 2026-09-10**: limit raised 1500Mi -> 2560Mi after measuring it at **942Mi at rest** (not the 244Mi seen earlier — the corpus grew during the day), i.e. already 63% of its ceiling before any load. Pod restarted clean at 265Mi, 0 restarts. **Not fully answered:** `load_vectors.py` rebuilds the entire ANN index every run (`indexed: 47829 articles`) and the corpus grows daily, so the ceiling will be reached again — the durable fix is incremental indexing, not a bigger number. Also unmeasured: which of the index rebuild or the 24,104-edge upsert actually causes the spike. Original text follows. ArangoDB is being OOM-killed roughly every few hours, and each kill silently breaks the dev tunnel | the graph store, the live test suite, and any deployment sized from these numbers | Measured on the cluster 2026-09-09: the `arangodb` pod shows **8 restarts in 2d2h**, `Last State: Terminated, Reason: Error, Exit Code: 137` — OOMKilled — against its own `limits.memory: 1500Mi`. This is **not** node pressure: the node was at 77% with about 3.2 GiB free at the time. The pod idles at 244Mi, so something in our own workload spikes it past 1500Mi; the vector index (47,829 articles x 384 dims, rebuilt by every `load_vectors.py` run) and the 23,855-edge `upsert_comovement` are the candidates, unmeasured as to which. **Two consequences beyond the restarts.** (1) Each kill invalidates the remote `kubectl port-forward`, so `localhost:19999` keeps listening while nothing answers — that turned 13 live tests into silent skips, caught only because Q-43's guard exists. Restarting the SSH leg is not enough; the remote forward must be restarted too. (2) `1500Mi` is already known-insufficient, so any deployment sizing that inherits it inherits the crash. Not answered: whether to raise the limit, bound the workload, or both — and raising a limit on the host that runs real-money trading is the user's call, not one to make from here. |
| Q-48 | `--dry-run` cannot catch the failure D-110 was built for | nothing today; the value of a dry run as a pre-flight check | `node_comovement` returns `{"done": ["comovement: DRY"]}` at `daily_ingest.py:130-131`, before it reaches `serve.COMOVE_CLOSES()` or `session_available`. So `--dry-run` prints four green nodes even when the real run would now error. That is exactly the blind spot that let tonight's incident through: the dry run passed all four nodes, and the real run still left the product worse. Every node has the same shape, so this is not specific to comovement — a dry run currently verifies that the *plumbing* is wired, not that the *data* can answer. Deciding what a dry run should mean is the real question; making it read the frame would cost a ~650MiB read, which may or may not be worth it for a pre-flight. |
| Q-47 | ~~`/graph` and `/movers` answer errors with HTTP 200~~ **ANSWERED by D-108** | nothing today; an HTTP-status-only client of the deployed service | D-102 gave `/followers` and `/network` real 400s via `parse_bounded`, and D-104 did the same for `/run`'s `limit`. But `/graph` and `/movers` still catch `Exception` and return `{"error": ...}` through `_json`, which hardcodes `send_response(200)` — so D-107's new `ValueError` surfaces as a 200 carrying an error body, and a caller reading only the status cannot tell bad input from a clean run. This is the same inconsistency between sibling endpoints that D-104 existed to close, one layer up. The SSE endpoints may not be able to join the rule: `/run` flushes its headers before `stream()` can raise, so its errors are structurally stuck in the body — which is itself worth deciding rather than inheriting. |
| Q-46 | ~~The live tests share fixed database names, so two concurrent suites collide~~ **ANSWERED by D-106** — fixed in `tests/conftest.py` alone, and the two grounds given below for not fixing it were both wrong: it *is* reproducible on demand, and it touched one file, not five. | `tests/conftest.py`, `tests/test_vector_index.py` and the four other live-gated files | Each live test file hardcodes its own database (`test_vector_index`, `test_arango_topology`, `test_market_scan`, `test_comovement_store`, `test_loader_idempotency`), and at least `test_vector_index.py` drops and recreates its `article` collection in the fixture. Two pytest runs against the same ArangoDB therefore race: one drops while the other inserts, and the second fails with a 409 unique-constraint on `chip-article`. Observed once today, when several agents each ran the suite at the same time; **not** reproducible in normal use — three consecutive single runs gave 168 passed. So it is a parallelism defect, not a correctness one, and it is logged rather than fixed because the fix touches five files for a condition a single developer never hits. It **would** bite parallel CI jobs, or anyone running tests while an agent does. Answered by giving `arango_db_or_skip` a per-process database suffix (`os.getpid()` or a uuid) and a teardown that drops it — noting the teardown is the part that needs care, since an abandoned run would otherwise leave databases behind. Related to Q-43, which fixed the opposite failure: tests that looked green while verifying nothing. This is the mirror image — tests that fail while nothing is wrong — and both erode the same thing. |
| Q-45 | Can the supply graph be deepened enough to test hop-dependent propagation at all? | D-92, D-88, D-78, `src/lagmatrix/edgar/relations.py` | D-92 could not answer its own question: only **4 of 105 suppliers (4%)** are themselves customers with suppliers, giving **6 hop-2 pairs** and a realised MDE of 0.39 against a 0.25 threshold. The graph is 76 depth-1 stars because only customers' 10-K concentration disclosures were ingested. Answered by ingesting the same disclosures for the 105 suppliers — the `edgar/relations.py` classifier and its migration script already exist and were audited at D-78, so this is acquisition, not new method — then re-running `scripts/experiment_hops_days.py` unchanged and re-reading its realised MDE. **Pre-commit before collecting:** the D-92 design, threshold and decision rule are re-used verbatim; deepening the graph must not be an excuse to re-specify the test. Worth knowing the ceiling first: if the second ingest still yields under ~50 hop-2 pairs, the MDE will stay above 0.25 and the question should be closed as unanswerable with 10-K-derived structure rather than pursued further. |
| Q-44 | Should the graph be reshaped so retrieval that has no data dependency can actually run concurrently? | D-89, D-35, `graph/builder.py` | D-89 establishes that `vector_retriever`'s position after `graph_retriever` is a scheduling artefact — it needs only `c.symbol` (D-83) — but that it cannot simply be moved, because `context_fusion`'s join fires once per superstep in which any in-edge fires, and `evidence`/`errors` use concatenating reducers. So the pipeline serialises two independent lookups and the live page's own latency numbers understate what the design could do. Answered by one of: making `fuse_evidence` idempotent so a double firing is harmless (the honest general fix, and the one that would also make the graph robust to future joins); reconsidering `defer=True`, which D-35 declined for reasons that predate this evidence; or deciding the serialisation is acceptable and saying so on the page rather than leaving the diagram to imply a dependency that does not exist. Not urgent: the measured cost is one superstep of wall-clock on runs that complete in ~3 seconds. |
| Q-43 | The 9 ArangoDB-dependent tests cannot pass in this environment and skip silently — how should live tests fail loudly instead? | `tests/test_arango_topology.py`, `tests/test_vector_index.py`, `tests/test_market_scan.py`, `scripts/serve.py:52` | Measured 2026-09-09, two independent faults, both rendering as a clean `skip`: **(a)** the tests default to `http://localhost:8529` (`test_arango_topology.py:57`) while the app defaults to `http://localhost:19999` (`serve.py:52`) — 8529 is closed, 19999 is the live tunnel; **(b)** pointed at the correct URL they get `[HTTP 401][ERR 11] bad username/password`, because the tests do not read the credential from `~/.lagmatrix-arango-pw` the way `serve.py:72` does. So the whole graph layer — `ArangoTopology`, `MarketScan`'s live path, `NewsIndex` — has never been exercised by a passing test here, while the suite reports `111 passed, 9 skipped` and looks healthy. This is the same pathology already seen once in this project (a stale listener made live tests skip rather than fail); the skip-if-unreachable guard is doing exactly what it was written to do, which is the problem. Related but distinct: **nothing under `tests/` imports `scripts/serve.py` or `scripts/capture_showcase.py` at all**, so a broken import there leaves the suite fully green — demonstrated 2026-09-09 when deleting `rank_by_room` broke `serve.py`'s import and the suite still reported 111 passed. Answered by deciding what a live test should do when the dependency is absent: skip is right for a laptop with no tunnel, but there is currently no mode in which its absence is an error, so nobody ever learns the tests are dead. Options: an opt-in `LAGMATRIX_REQUIRE_LIVE=1` that converts skip to failure, aligning the default URL and credential lookup with `serve.py`'s, and a one-line import smoke test for the two scripts. |
| Q-42 | If the supply graph is a correlation filter with extra steps, what does the GraphRAG premise actually buy? | D-88, D-74, `adapters/arango.py`, `scripts/serve_index.html` | D-88 shows the contemporaneous linked-vs-control co-move is fully explained by trailing correlation, with a *negative* residual (−0.093, z=−2.98; −0.317, z=−2.54 on replication). The live page presents the supply graph as the thing that finds non-obvious candidates. If a correlation screen selects the same names more cheaply, that framing needs to change or be defended. Three things the graph plausibly still buys, none yet measured: **direction** (the sign of the thesis, which correlation alone does not give), **an interpretable rationale** (a filing sentence a human can check, which is the actual product), and **candidates a correlation screen would rank too low to surface**. Answered by running the scan with the supply traversal replaced by a top-k trailing-correlation screen on the same dates and comparing the candidate sets and their forward returns — if the sets largely coincide and neither predicts, the graph is doing presentational work, which is a legitimate answer but a different claim from the one the page makes. Note the binding constraint from D-88's consult: **chains, not dates** — 105 distinct suppliers across 67 leaders cannot resolve a D-74-sized effect at any date count. |
| ~~Q-41~~ | The `responded` bucket never fires — is `x >= y` the wrong bar for "already moved too much to enter"? | D-84, D-87 | **The premise was wrong, and the correction matters more than the question.** This was logged from a single scan date (2026-05-11, 0 of 19) and generalised into a structural claim. Measured over 432 dates, `responded` fires on **7.7%** of supplier-events; reproduced on the production code path over 13 sampled dates at **4 of 76 (5.3%)**, firing on 3 of those 13 dates. 69% of dates with >= 8 candidates have zero `responded`, so 0-of-19 is the *modal* outcome, not an anomaly (P = 0.22 under independence), and 40% of dates have a max ratio below that scan's 0.61. The bar was never the problem. Answered by D-87, which deletes the bucket for an entirely different and measured reason — non-predictiveness — not for being unreachable. Lesson worth keeping: one date is not a sample, and this entry asserted a property of the design from n=1. |
| ~~Q-40~~ | Are `lag_response` and correlation `leader_move` evidence independent enough to sit in one weighted sum? | D-84, `graph/nodes/context_fusion.py`, Q-12 | `Y` itself never double-counts — Q-37's `signal_universe` fix keeps the originating leader out of `X`'s own correlation pool — but a *third* symbol highly correlated with `Y` still contributes an ordinary `leader_move` unit alongside the `lag_response` unit, and the two are not weighted against each other. The independence discount (Q-12) operates within the correlation bloc only; it does not see `lag_response` at all, so a candidate discovered from `Y` and also neighboured by `Y`'s bloc can reach `MIN_EFFECTIVE` on what is arguably one observation counted twice. **Sharpened 2026-09-08 — when the two do land on the same symbol the interaction is not merely un-weighted, it is destructive, and it was demonstrated, not theorised.** Building PHASE-4's fixtures through a real `retrieve_neighbourhood` put `Y` in `X`'s own correlation top-k, and the two units then collide: in the *opposed* case `leader_move(Y, supports=True)` (that check reads the leader's sign, never the candidate's) exactly cancels `lag_response(Y, supports=False)`, `w_pro == w_con == 1.0`, and a genuine reversal reads **neutral instead of contradicted**; in the *responded* case the lone surviving `leader_move(Y, supports=False)` makes it read **contradicted** — precisely the spent-vs-refuted conflation D-84 exists to prevent. Reproduced directly, not inferred. **Latent, not live:** on the real 2026-05-11 scan, 0 of 19 candidates had their `origin_leader` appear as a `leader_move` unit, because Q-37's `signal_universe` union keeps shocked leaders out of every candidate's correlation pool. But that is the *only* thing preventing it, set at two call sites (`serve.py`, `pipeline/runner.py`); any caller that builds a `MarketScan` without that union reintroduces both failures silently. It also forced PHASE-4's fixtures to hand-build `lag_edges` rather than call `retrieve_neighbourhood`, so Success Criterion 4's "end-to-end" is satisfied from `leader_state` onward, not from retrieval — the plan's own PHASE-4 halt condition prescribes exactly this remedy. Answered by extending the cluster-size discount to cover the origin leader's bloc, or by making the exclusion an invariant of `MarketScan` itself rather than a caller's responsibility. **Moot as of D-87 (2026-09-09).** `lag_response` no longer exists, so there is no second unit for a `leader_move` unit to be summed with or cancelled against, and the destructive cancellation demonstrated above cannot occur. Closed by deletion rather than by resolution — the underlying general question (Q-12: correlated neighbours are one observation seen several times, and the independence discount only operates within the correlation bloc) is unchanged and remains open. Worth keeping on the record because the reproduction was real: it is the reason PHASE-4's fixtures could not call `retrieve_neighbourhood`, a limitation D-87's replacement removes, since `description` emits no `Evidence` to collide with anything. |
| Q-39 | `LagEdge.beta` carries two incompatible quantities, and the correlation one looks inverted — which is right? | `graph/nodes/graph_retriever.py:62`, `adapters/arango.py:63`, PLAN-2026-09-08-unresponded-lag | Two defects, both currently latent. **(a)** On a correlation edge `beta = corr * std(leader) / std(cand)`; on a supply edge it is `pct_revenue / 100`, an accounting ratio. One field, a volatility ratio and a revenue share, distinguished only by `relation`. **(b)** The correlation form is the reciprocal of the conventional beta for predicting the candidate from the leader (`corr * std(cand) / std(leader)`), so it appears inverted for the direction the pipeline cares about. Neither bites today: `grep -rn '\.beta\b' src/ tests/ scripts/` finds exactly one reader, a test asserting the supply edge's `pct_revenue`. Nothing in production reads it. The unresponded-lag work deliberately computes `room` from z-scores alone so it never touches `beta` — which is why this is logged rather than fixed inline. Answered by deciding what `beta` is *for*: if it is the transfer coefficient the lag hypothesis would want, it needs one meaning, the right orientation, and a test; if nothing will read it, it should be removed rather than left as a trap. |
| Q-37 | Does `pipeline/runner.py` have the same leader re-entry hole as the live UI did? | D-23, `pipeline/runner.py`, REQ-7 | `runner.py:80` builds `signal_universe = {c.symbol for c in signals.candidates()}` — the identical pattern `serve.py` had before PHASE-5 fixed it. It is not a live bug today, because `runner.py` type-hints `signals: ExternalSignals | None` and no caller passes a `MarketScan`. It becomes one the moment scan mode is wired into the batch runner. Answered by either unioning `shocked_leaders()` there too, or by making the runner refuse a `MarketScan` until it does. **Answered 2026-09-08 by unioning.** It was worse than latent: line 80
called `signals.candidates()` with no `as_of`, which `MarketScan.candidates(as_of: date)` cannot
accept, so a scan source raised `TypeError` there — and it recomputed the whole candidate list a
second time, meaning a second full 3,204-symbol sweep. Fixed by capturing the list once (preserving
that `signal_universe` is built from *all* candidates, not the `limit`-truncated ones) and unioning
`shocked_leaders(as_of)` duck-typed, matching `serve.py`. Duck-typed rather than on the
`CandidateSource` protocol, so `ExternalSignals` is not forced to expose scan-only machinery. One
pre-existing test changed with it: `test_run_uses_injected_signals_for_both_candidate_lookups`
pinned the double call as correct and now pins a single lookup. |
| Q-38 | Should `leader_state.py` use a non-overlapping baseline like `shocks.standardised_moves` specifies? | `graph/nodes/leader_state.py`, `adapters/candidates.py`, D-23 | `MarketScan` reads `standardised_moves`' contract literally — *"baseline must end strictly before returns begins"* — and passes adjacent, non-overlapping windows. `leader_state.py` passes an overlapping one, so its sigma is estimated from a sample that includes the move being measured, which shrinks the z-score of exactly the shocks it is looking for. The two now disagree about the same function. Deliberately not fixed here: changing it moves every published corroboration-mode result. Answered by measuring how much the z-scores differ on real data, then deciding whether the historical results need re-running. **Attempted 2026-09-08 and parked — it is bigger than the z-scores suggest.** The
window fix itself is three lines (patch kept at `q38-leader_state.patch`), but: (a) it forces a
rename, since `from lagmatrix import shocks` shadows `leader_state`'s local `shocks: list[Shock]`
and raises `UnboundLocalError`; (b) the new window needs `trail + move_win` sessions, and several
committed fixtures were sized for `trail` alone — `_shocked_closes` (62 rows), `_self_edge_closes`,
and `test_fanout`'s two-bloc fixture — so `returns.iloc[ti - 63 : ti - 3]` comes out **empty**, not
merely shifted, and the node produces no shocks at all; (c) that cascades into 9 failures across
`test_nodes`, `test_fanout` and `test_review`, plus the expected baseline move
(`2026-04-27 SYNA up: n_supporting 16 -> 20`). So the real work is lengthening fixtures so they
stay meaningful rather than merely passing — a test change, and the reason this was parked rather
than rushed alongside a commit. The measured "99% of classifications unchanged" holds for real
data with 159 sessions, where the history bound never binds; it does not describe short unit
fixtures. |
| Q-36 | `capture_showcase.py` and `lagmatrix.edgar.relations` now split sentences and read percentages differently — which is canonical? | D-78, `scripts/capture_showcase.py`, `src/lagmatrix/edgar/relations.py` | Two fixes went into the capture script for the page and not into the tested module: (a) the initials guard `(?<![A-Z])` blocked splitting after "Form 10-K.", gluing an unrelated clause to CGNX's disclosure; (b) "10% or more" is the ASC 280 *threshold*, not the counterparty's share — JBL's filing says "10% or more" then tables Apple at 11%, so the stored `pct_revenue=10` is wrong and the page now says 11. The duplication is the real defect: the page and the database disagree about the same filing. Answered by fixing both in `relations.py` under TDD and having `capture_showcase.py` import `classify` instead of carrying its own copy, then re-running the reclassification. |
| ~~Q-35~~ | Do D-73/D-74's supply-chain nulls hold on the audited 818-edge graph? | D-73, D-74, README | Both pre-registrations ran when 31% of edges were competitor lists, acquisitions and reversed relations (D-78). Re-running is cheap — the scripts exist and the edges are reloaded. Direction of the error is knowable (dropping non-supply edges removes noise, so a null stays null or sharpens; it cannot flip to a false positive this way), but the magnitude is not, and D-74's pooled estimate of +0.0000 was computed over chains that partly did not exist. Answered by re-running `experiment4.py`/`experiment5.py` against the reclassified graph and comparing b, z and I². **Answered 2026-09-07:** yes — pooled b = -0.0015 (was +0.0000), I² = 44% (was 0%), still far below the 0.02 threshold. The null holds; its heterogeneity rose. |
| ~~Q-34~~ | Does the directed supply-chain edge predict, where correlation did not? | D-72, D-63, spike 14 | The whole reason for building it: correlation is symmetric and was a powered null, co-mention is undirected, this is neither. Untestable until the relation labels are trustworthy (needs the LLM pass, hence an API key) and the crawl is wide enough that supplier-side edges reach the alert tickers. Must be pre-registered exactly as D-63 was — the graph looking economically sensible is not evidence, which is what correlation taught. **Answered by D-73/D-74: no effect detectable, estimate stable at zero, but underpowered against a 0.02 threshold.** Direction did not rescue the mechanism — though unlike the correlation nulls, this one shows no heterogeneity and no artefacts, so it is a clean measurement rather than a contested one. |
| ~~Q-01~~ | How is the leader→lagger topology built in the first place? | — | Answered by D-16: derived from Alpaca bars (statistical lag) and News API co-mention, recomputed on trailing windows. Supply-chain sourcing abandoned. Residual question is edge *quality* → Q-14. |
| ~~Q-02~~ | Does Qdrant filtered search hold the latency budget with `symbol IN (...)` + recency filter? | D-03 | Moot — Qdrant dropped. Answered by D-13; the filtering concern survives as Q-09 |
| ~~Q-03~~ | What is the end-to-end latency budget? | — | Largely dissolved by D-15: a daily cadence gives hours, not milliseconds. Survives only as a scheduling concern. |
| ~~Q-04~~ | Is an LLM in the hot path viable? | D-05 | Answered by D-15: yes. At a daily cadence `claude-opus-5` at high effort on every signal is affordable. This was the tightest constraint and it is gone. |
| Q-05 | Shock definition: sigma over what baseline — trailing intraday vol, overnight-adjusted, or sector-relative? | `shock_detector.py` | Naive sigma will fire on every open and every scheduled news event |
| Q-06 | How do we avoid firing on shocks that are *already* priced into the lagger? | `signal_analyst.py` | Needs the lagger's own concurrent move as a feature, not just the leader's |
| Q-07 | What exactly is the outcome label for a fire — sign of forward return, excess over benchmark, or factor-neutral residual? | D-20, evaluation | **Re-scoped.** The harness *shape* was answered by D-20 (replay, split corroborated vs contradicted, score on calibration), so this is no longer "whole project" blocking. What remains is the definition of "right", which decides what the whole evaluation measures — and per spike 03 §4 an un-neutralised label would let factor beta masquerade as signal. |
| ~~Q-08~~ | Checkpointer choice, and whether durable graph state is needed | `graph/builder.py` | Answered by PHASE-3 / D-45: **SqliteSaver**. `InMemorySaver` would deliver neither motivation (it dies with the process, so the crash case is unrecoverable and there is nothing to replay tomorrow); Postgres has not earned a server process at this scale. |
| Q-09 | ~~Can ArangoDB **pre-filter** a vector search by `symbol IN (...)` + recency, or only post-filter?~~ **ANSWERED 2026-09-10: it pre-filters.** | D-13, `adapters/vector.py` | Sources contradict (issue #21690 vs v3.12.6 optimizer rule vs 3.13 docs) and `docs.arango.ai` 403s to fetches. Stand up a local instance and try the real query. **De-escalated by D-15** — with hours of budget, a large LIMIT plus post-filtering is acceptable, so this no longer gates D-13 on latency, only on correctness. **Settled 2026-09-10 by reading the plan rather than the docs**, which is what should have happened when the sources first contradicted each other: `db.aql.explain()` on `adapters/vector.py`'s `_SEARCH_AQL` shows **no `FilterNode` at all** — the optimiser folds `LENGTH(INTERSECTION(a.symbols, @symbols)) > 0 AND a.date < @as_of` into `EnumerateNearVectorNode` as a `filter` attribute, so the predicate is applied *inside* the ANN scan. Found by accident while chasing what looked like a silently dropped point-in-time guard (D-124). This matters more than latency: a post-filter would have starved thin symbols worse as the corpus grew, so D-124's widening would have degraded exactly the symbols it was meant to help. Measured at 74ms against 80,589 documents. |
| ~~Q-10~~ | Does an exploitable lead-lag exist at *minute* scale, or only daily-to-monthly as the literature documents? | `shock_detector.py`, D-05, Q-03 | The design assumes `lag_minutes`; the evidence base (Cohen & Frazzini, "A frog in every pan", Network Momentum) is daily-to-monthly. Answered by D-15: no evidence for minute-scale supply-chain leads; retargeted to days. |
| ~~Q-11~~ | Which mode is the product? | — | Answered by D-18: a corroboration/odds layer over an exogenous signal — closest to spike 02's red-team framing, with lagger-entry as the mechanism. |
| Q-13 | Does `LagEdge` need an `as_of` field? | `domain/models.py` | Largely resolved by D-16 — Alpaca-derived edges are point-in-time by construction. Still add `as_of` as the observation date so the backtest can slice the graph at t. Now a small design task, not a bias risk. |
| ~~Q-14~~ | How complete and accurate is Benzinga's `symbols` tagging, and what article-breadth cutoff kills hub noise? | `adapters/vector.py`, edge quality. **Answered by D-70:** breadth <= 8 (p90) plus PMI. Unfiltered, 2.1% of articles carry 95.2% of pairs; unnormalised, the ranking is popularity. Formerly: edge quality | A "chip stocks rally" piece tagging 25 tickers generates ~300 spurious pairs. Sample a few hundred articles by hand. Determines whether co-mention edges are usable at all. |
| Q-12 | How do we measure *effective independent* evidence in a neighbourhood rather than counting correlated neighbours? | `context_fusion.py`, `signal_analyst.py` | Meucci's Effective Number of Bets (entropy over uncorrelated factors / Minimum-Torsion Bets) is the candidate. Needs a covariance estimate over the neighbourhood — decide the window and shrinkage. Without this, any confluence score is overconfident by construction. |
| Q-15 | Is the graph feature actually orthogonal to the upstream signal? | D-21, whole design | Answered in part: the signal is **technical + news-driven**, overlapping both edge modalities. D-21 argues the orthogonal axis is own-name vs neighbourhood. Now an empirical test on the replay set, not an open design question. |
| ~~Q-18~~ | Is the upstream signal codified well enough to replay? | D-20 | **Moot.** Answered by spike 05: no — it is human options alerts from 10 authors, not a function. But replay is unnecessary; 284 real fires exist. See D-26. |
| ~~Q-16~~ | Do we have a history of past signal fires with outcomes? | evaluation | Answered: a few dozen — far too few (detects only ~40pp differences at 80% power). Superseded by D-20: manufacture the set by replaying the signal over history. |
| ~~Q-20~~ | Do index-ETF candidates get excluded? | D-26 | Answered by spike 06: **no — kept**, with breadth/dispersion as their feature instead of diffusion. See D-28. The premise that an index has no neighbourhood was wrong; it has a different one. |
| Q-25 | Is the ~1-trading-day holding horizon real, on more than 20 trades? | D-32, D-15, any future label | `hold_minutes` exists only on `trade_context`'s 31 rows (20 with a hold), covering 3 weeks. Reconstruct holding periods for all 98 fires from `EntryFilled` -> `PositionClosed` timestamps in `audit_log` to confirm. Gates the label horizon of every future test. |
| ~~Q-24~~ | Can the signal feed be widened beyond 10 authors / 2 channels? | D-30, dataset size | Spike 08: the dataset grows ~30 fires/month and that rate is the binding constraint on ever reaching statistical power. Adding sources scales it linearly — the only lever that shortens an 8-to-23-month timeline, and worth more than any modelling improvement. Owner question for oh-my-tradeagent. **Answered by spike 13 / D-56:** yes, and it is one env var per channel with no author filter to relax — but the linear-scaling premise was wrong. 76% of the feed is a single author, so the yield of a new channel depends entirely on whether a prolific alerter posts there. |
| Q-28 | Is the evaluation set generalisable, or is it one trader's selection style? | D-31, D-33, spike 13 | TradingTheTrend is 76% of all BTO fires, so a result from either pre-registration describes that account rather than "options alerts". Waiting cannot fix this — it accumulates more of the same author. Answered by getting a second high-volume source and re-running the pre-registered test per-author, or by reporting every result as single-source and scoping the claim accordingly. |
| ~~Q-29~~ | Should the *daily* pipeline exclude funds from neighbour selection? | D-27, D-43, `graph_retriever.py`, D-31, D-33 | D-60 found 78% of neighbourhood slots are funds, some holding the candidate. Excluding them would change every verdict in the baseline and both pre-registered results, so it is not a free fix: it re-opens D-31/D-33 rather than improving them. Answered by measuring how much of the current evidence comes from funds that hold the candidate — which needs holdings data Alpaca does not provide (Q-22) — or by a correlation-threshold proxy for containment. **Answered by D-62: exclude them.** The deciding argument was construct validity, not measured performance. |
| ~~Q-31~~ | Has the pipeline's actual feature ever been tested? | D-31, D-33, `context_fusion.py`, D-62 | No. D-31 pre-registered `max abs(neighbour z) - abs(candidate z)` and the experiments implement exactly that, faithfully. The pipeline instead computes an independence-weighted, direction-matched sum — Q-12's discounting, the project's distinctive idea — and no test has ever evaluated that statistic. The two are different features, so both pre-registered nulls are silent about the thing that actually ships. Answered by a fresh pre-registration on the shipped feature, which needs power this dataset does not have (D-58), or by testing it on synthetic candidates over a decade of bars where n is not the constraint. |
| ~~Q-32~~ | Does the feature carry information *conditional* on an alert-worthy setup? | D-63, spike 14, D-31, D-33 | The only surviving form of the hypothesis. D-63 tested random (symbol, date) pairs and found nothing above ~18bp, but that is not the population the product serves — real alerts are on names where something is already happening. Testing this needs either a much larger signal feed (D-56, D-58) or a defensible synthetic definition of 'alert-worthy', which risks encoding the answer into the selection. Materially harder than what D-63 settled. |
| Q-33 | Does the ~10bp edge survive real option execution costs? | D-65, D-64, Q-22 | D-65 shows it is 4-6% of premium at 120x leverage, i.e. the same order as spreads rather than below them, which is why it matters. Settling it needs historical option quotes — bid, ask, and fills — which Alpaca does not provide on this tier (Q-22 records the same gap for holdings). Until then the trading case is unpriced in both directions. |
| Q-30 | Should repeat same-day alerts on one symbol be assessed separately? | D-61, `graph/state.py`, D-31, D-33 | `candidate_key` collapses them, so 102 fires are ~87 assessed branches and duplicates carry copies of one verdict. Now that `as_of_ts` exists, splitting them is possible — a second alert hours later sees a different neighbourhood state and is arguably a distinct observation. It would change the baseline and re-open D-31/D-33, so it is a real decision, not a cleanup. Answered by measuring how often the neighbourhood state actually differs between same-day repeats. |
| Q-23 | Will `trade_context` backfill or keep growing, and will `realized_pnl` ever be populated? | D-26, evaluation | 31 rows over 3 weeks, `realized_pnl` populated on **zero** of them. Its schema (Greeks, `underlying_spot`, MFE/MAE) is exactly what the evaluation wants. If it grows it becomes the evaluation table; if not it stays a template. Owner question for oh-my-tradeagent, not this repo. |
| Q-22 | Where do ETF constituent weights come from, given Alpaca has no holdings endpoint? | D-16, D-28 | First real conflict with the Alpaca-only constraint. Likely a small static weights file for 2-3 ETFs (~100 lines). Prefer equal-weighted breadth over cap-weighted contribution — it is far less sensitive to weight drift, so point-in-time exposure stays small. |
| ~~Q-21~~ | Option P&L or underlying forward return as the label? | Q-07 | Answered by spike 07 / D-29: **underlying forward return.** Decided partly by data — no underlying spot is stored anywhere except `trade_context`'s 31 rows, and option P&L exists only for the ~60 filled fires. |
| ~~Q-26~~ | Should `graph_retriever` exclude the candidate's own column from the correlation pool? | `graph_retriever.py`, D-23 | **Latent bug found by phase6-red.** `corrwith` does not drop the candidate itself, so it ranks as its own leader at ρ=1.0; `leader_state`'s `or sym == c.symbol` then always emits a self-shock, and `context_fusion` can never satisfy `abs(cand_z) < abs(z)` against an identical value — one guaranteed contradicting unit per candidate. Production is **unaffected** only because D-27 excludes the 24 alert tickers, which happen to include every candidate: verified NFLX 2026-06-02 has no self-edge in production and one in a `signal_universe=set()` fixture. That is accidental correctness. Under `MarketScan` (D-23), where candidates are discovered rather than drawn from the alert feed, every candidate would contradict itself. Fix is one line; out of PHASE-6's scope. **Answered by D-54.** The bug was worse than recorded here: the self-edge also *displaced* a genuine neighbour from the top-k, so every candidate silently lost its lowest-ranked real neighbour, and two tests turned out to be passing because of it. |
| Q-19 | Which model labels co-mention articles at volume — `claude-opus-5`, or something cheaper for bulk? | D-05, D-17, `adapters/llm.py` | D-17 made bulk relationship-labelling the LLM's primary job; ~11 years of Benzinga news is a different order of magnitude from one call per signal. Answered by estimating article count after the Q-14 breadth filter, then pricing both options. |
| ~~Q-27~~ | How does a reader reproduce `baseline-98.csv` without our `bars.parquet`? | D-49, `scripts/check_baseline.py` | Answered by D-50, then unanswered by D-51: the projections were withdrawn from the repo, so the honest answer is that a reader cannot reproduce it — they read the decision log instead. Kept struck because the *question* is settled; the resolution is deliberate, not pending. Original finding stands: Q-27 named one missing input; there were two, and the second (`data/fires.csv`, reached through an uninjected second call site in `run()`) was found only by running from a tracked-files-only tree. The three local checks all passed while it was broken. |
| ~~Q-17~~ | Does the upstream signal's horizon match the graph horizon? | D-15 | **Answered wrongly, then corrected.** Spike 05 used days-to-expiry (median 8); the right field is holding period. `hold_minutes` gives a median of ~22 hours. Superseded by D-32; reopened as Q-25. |

---

## Spike Index

| # | Spike | Answers | Status |
|---|-------|---------|--------|
| 01 | [ArangoDB as the GraphRAG substrate, and who has built this already](01-arangodb-graphrag-prior-art.md) | Q-02 (closed), raised Q-09 and Q-10 | done — desk research only, nothing benchmarked |
| 02 | [Inverting the pipeline: graph as confluence/evidence for a chosen equity](02-confluence-inversion.md) | raised Q-11 and Q-12 | done — analysis only, no code or data |
| 03 | [Adversarial review: the case that LagMatrix does not work](03-adversarial-review.md) | raised Q-13; re-prioritised Q-01 and Q-07 | done — argument only, nothing falsified yet |
| 04 | [Constraining to Alpaca: what dies, and what it accidentally fixes](04-alpaca-only-graph.md) | closed Q-01, largely closed Q-13, raised Q-14 | done — API capability research, no data pulled |
| 05 | [oh-my-tradeagent's audit_log as the upstream signal history](05-oh-my-tradeagent-signal-history.md) | closed Q-16, Q-17, Q-18; raised Q-20, Q-21 | done — read-only queries against live prod |
| 06 | [ETFs as candidates: reversing Q-20](06-etfs-as-candidates.md) | closed Q-20 (reversed spike 05), raised Q-22 | done — literature + API research |
| 07 | [Full sweep of the homelab Postgres, and a correction to spike 05](07-db-sweep.md) | **corrected spike 05 (284 fires -> 98)**, closed Q-21, raised Q-23 | done — read-only aggregate queries |
| 08 | [Cluster-wide sweep: the second Postgres, and the accumulation rate](08-cluster-wide-data-sweep.md) | found `databases/pg-main-1`; established ~30 fires/month; raised Q-24. **Its linearity claim is corrected by spike 13** — the feed is one author, not ten | done — read-only |
| 09 | [The pre-registered test: null, and why it is uninformative](09-first-test-result.md) | executed D-31 (null, n=2 fired); **corrected Q-17 via D-32**; raised Q-25 | done — pilot, not evidence |
| 10 | [The second pre-registered test: directionally right, statistically silent](10-second-test-result.md) | executed D-33 (+7.2pp, z=0.60, MDD 34.2pp) | done — **inconclusive; stopping rule invoked** |
| 11 | [LangGraph idioms: what the current build gets wrong](11-langgraph-idioms.md) | produced D-35 | done — docs research against langgraph 1.2.11 |
| 12 | [LangGraph 1.2.11 behaviour, probed rather than read](12-langgraph-v1-behaviour.md) | evidence for D-44/D-46/D-47; records 8 failed claims | done — 14 probes, all re-probed during execution |
| 13 | [Widening the signal feed: what the 30/month is actually made of](13-widening-the-signal-feed.md) | answered Q-24; corrected spike 08's linearity claim; raised Q-28 | done — read-only |
| 14 | [The shipped feature, tested properly, does not predict](14-shipped-feature-null.md) | answered Q-31 with a powered null; raised Q-32 | done |
| 15 | [What the signal feed actually earned](15-realized-pnl.md) | first measurement of the signal itself; +$37.6k, t=1.31, ETFs lose | done — read-only |
