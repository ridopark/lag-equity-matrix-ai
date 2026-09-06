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
- **Outcome:** pending — `MarketScan` is a documented stub raising
  NotImplementedError; the claim that no node needs branching is untested until
  it is written.
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

## Open Questions

| ID | Question | Blocks | Notes |
|----|----------|--------|-------|
| ~~Q-01~~ | How is the leader→lagger topology built in the first place? | — | Answered by D-16: derived from Alpaca bars (statistical lag) and News API co-mention, recomputed on trailing windows. Supply-chain sourcing abandoned. Residual question is edge *quality* → Q-14. |
| ~~Q-02~~ | Does Qdrant filtered search hold the latency budget with `symbol IN (...)` + recency filter? | D-03 | Moot — Qdrant dropped. Answered by D-13; the filtering concern survives as Q-09 |
| ~~Q-03~~ | What is the end-to-end latency budget? | — | Largely dissolved by D-15: a daily cadence gives hours, not milliseconds. Survives only as a scheduling concern. |
| ~~Q-04~~ | Is an LLM in the hot path viable? | D-05 | Answered by D-15: yes. At a daily cadence `claude-opus-5` at high effort on every signal is affordable. This was the tightest constraint and it is gone. |
| Q-05 | Shock definition: sigma over what baseline — trailing intraday vol, overnight-adjusted, or sector-relative? | `shock_detector.py` | Naive sigma will fire on every open and every scheduled news event |
| Q-06 | How do we avoid firing on shocks that are *already* priced into the lagger? | `signal_analyst.py` | Needs the lagger's own concurrent move as a feature, not just the leader's |
| Q-07 | What exactly is the outcome label for a fire — sign of forward return, excess over benchmark, or factor-neutral residual? | D-20, evaluation | **Re-scoped.** The harness *shape* was answered by D-20 (replay, split corroborated vs contradicted, score on calibration), so this is no longer "whole project" blocking. What remains is the definition of "right", which decides what the whole evaluation measures — and per spike 03 §4 an un-neutralised label would let factor beta masquerade as signal. |
| ~~Q-08~~ | Checkpointer choice, and whether durable graph state is needed | `graph/builder.py` | Answered by PHASE-3 / D-45: **SqliteSaver**. `InMemorySaver` would deliver neither motivation (it dies with the process, so the crash case is unrecoverable and there is nothing to replay tomorrow); Postgres has not earned a server process at this scale. |
| Q-09 | Can ArangoDB **pre-filter** a vector search by `symbol IN (...)` + recency, or only post-filter? | D-13, `adapters/vector.py` | Sources contradict (issue #21690 vs v3.12.6 optimizer rule vs 3.13 docs) and `docs.arango.ai` 403s to fetches. Stand up a local instance and try the real query. **De-escalated by D-15** — with hours of budget, a large LIMIT plus post-filtering is acceptable, so this no longer gates D-13 on latency, only on correctness. |
| ~~Q-10~~ | Does an exploitable lead-lag exist at *minute* scale, or only daily-to-monthly as the literature documents? | `shock_detector.py`, D-05, Q-03 | The design assumes `lag_minutes`; the evidence base (Cohen & Frazzini, "A frog in every pan", Network Momentum) is daily-to-monthly. Answered by D-15: no evidence for minute-scale supply-chain leads; retargeted to days. |
| ~~Q-11~~ | Which mode is the product? | — | Answered by D-18: a corroboration/odds layer over an exogenous signal — closest to spike 02's red-team framing, with lagger-entry as the mechanism. |
| Q-13 | Does `LagEdge` need an `as_of` field? | `domain/models.py` | Largely resolved by D-16 — Alpaca-derived edges are point-in-time by construction. Still add `as_of` as the observation date so the backtest can slice the graph at t. Now a small design task, not a bias risk. |
| Q-14 | How complete and accurate is Benzinga's `symbols` tagging, and what article-breadth cutoff kills hub noise? | `adapters/vector.py`, edge quality | A "chip stocks rally" piece tagging 25 tickers generates ~300 spurious pairs. Sample a few hundred articles by hand. Determines whether co-mention edges are usable at all. |
| Q-12 | How do we measure *effective independent* evidence in a neighbourhood rather than counting correlated neighbours? | `context_fusion.py`, `signal_analyst.py` | Meucci's Effective Number of Bets (entropy over uncorrelated factors / Minimum-Torsion Bets) is the candidate. Needs a covariance estimate over the neighbourhood — decide the window and shrinkage. Without this, any confluence score is overconfident by construction. |
| Q-15 | Is the graph feature actually orthogonal to the upstream signal? | D-21, whole design | Answered in part: the signal is **technical + news-driven**, overlapping both edge modalities. D-21 argues the orthogonal axis is own-name vs neighbourhood. Now an empirical test on the replay set, not an open design question. |
| ~~Q-18~~ | Is the upstream signal codified well enough to replay? | D-20 | **Moot.** Answered by spike 05: no — it is human options alerts from 10 authors, not a function. But replay is unnecessary; 284 real fires exist. See D-26. |
| ~~Q-16~~ | Do we have a history of past signal fires with outcomes? | evaluation | Answered: a few dozen — far too few (detects only ~40pp differences at 80% power). Superseded by D-20: manufacture the set by replaying the signal over history. |
| ~~Q-20~~ | Do index-ETF candidates get excluded? | D-26 | Answered by spike 06: **no — kept**, with breadth/dispersion as their feature instead of diffusion. See D-28. The premise that an index has no neighbourhood was wrong; it has a different one. |
| Q-25 | Is the ~1-trading-day holding horizon real, on more than 20 trades? | D-32, D-15, any future label | `hold_minutes` exists only on `trade_context`'s 31 rows (20 with a hold), covering 3 weeks. Reconstruct holding periods for all 98 fires from `EntryFilled` -> `PositionClosed` timestamps in `audit_log` to confirm. Gates the label horizon of every future test. |
| ~~Q-24~~ | Can the signal feed be widened beyond 10 authors / 2 channels? | D-30, dataset size | Spike 08: the dataset grows ~30 fires/month and that rate is the binding constraint on ever reaching statistical power. Adding sources scales it linearly — the only lever that shortens an 8-to-23-month timeline, and worth more than any modelling improvement. Owner question for oh-my-tradeagent. **Answered by spike 13 / D-56:** yes, and it is one env var per channel with no author filter to relax — but the linear-scaling premise was wrong. 76% of the feed is a single author, so the yield of a new channel depends entirely on whether a prolific alerter posts there. |
| Q-28 | Is the evaluation set generalisable, or is it one trader's selection style? | D-31, D-33, spike 13 | TradingTheTrend is 76% of all BTO fires, so a result from either pre-registration describes that account rather than "options alerts". Waiting cannot fix this — it accumulates more of the same author. Answered by getting a second high-volume source and re-running the pre-registered test per-author, or by reporting every result as single-source and scoping the claim accordingly. |
| Q-29 | Should the *daily* pipeline exclude funds from neighbour selection? | D-27, D-43, `graph_retriever.py`, D-31, D-33 | D-60 found 78% of neighbourhood slots are funds, some holding the candidate. Excluding them would change every verdict in the baseline and both pre-registered results, so it is not a free fix: it re-opens D-31/D-33 rather than improving them. Answered by measuring how much of the current evidence comes from funds that hold the candidate — which needs holdings data Alpaca does not provide (Q-22) — or by a correlation-threshold proxy for containment. |
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
