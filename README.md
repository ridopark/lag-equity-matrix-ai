# LagMatrix

[![ci](https://github.com/ridopark/lag-equity-matrix-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/ridopark/lag-equity-matrix-ai/actions/workflows/ci.yml)

**Does the market's topology corroborate a trading signal you already have?**

LagMatrix takes a candidate that something else picked, reconstructs that name's
correlation neighbourhood *as of that date*, checks whether its neighbours have
already moved while the candidate has not yet repriced, optionally pulls the news
linking them, and returns a two-sided assessment — including the case *against*.

It does not originate buy/sell decisions (D-18), and it does not emit a
probability. `odds_adjustment` is hard-coded to `0.0` because no powered test
supports a number (D-34). The verdict is a description of evidence, not a
forecast.

## What it actually found

Over 102 real signals across 24 tickers:

| verdict | n |
|---|---|
| neutral | 62 |
| corroborated | 21 |
| contradicted | 16 |
| no_assessment | 3 |

**The premise does not hold.** Four different ways of defining "related
company" were built and tested. **None predicts returns** at the horizons this
signal feed trades. Two of them do predict something else — whether a measured
correlation *persists* — which is a weaker claim and is kept carefully separate
below.

### 1. Correlation — the shipped edge

Neighbours ranked by return correlation over a trailing 60 sessions.

| test | result |
|---|---|
| unconditional, 2-session horizon | **−4.6 bp**, z=−0.72, **MDE 17.9 bp** — a *powered* null |
| intraday lead-lag, 1-minute bars | **null**, twice; peers move *with* the candidate, 89% at lag 0 |
| conditional on mega-cap | +10.4 bp, z=+1.28 — underpowered, CI [−5.5, +26.3] |

Correlation is **symmetric**, which is why the intraday test found lag 0: you
cannot select neighbours by "moves together" and then be surprised they move
together. It also selects funds — 78% of neighbourhood slots were index ETFs
holding the candidate, until D-62 excluded them.

### 2. News co-mention — a bounded null, and a conditional signal

229,737 Benzinga articles (2014→2026) in Postgres. Two corrections were needed
before the edge meant anything: 2.1% of articles tag more than 20 symbols and
contribute **95.2% of all co-mention pairs**, and raw counts rank *popularity*
rather than relationship. With a breadth cutoff at p90 and PMI, NVDA's peers
become CRWV, ARM, TSM, MRVL, SFTBY, DELL, AMD — foundry, customers,
competitors. Correlation gave NVDY, DSI, QGRW, SPYG, VOOG.

| test | result |
|---|---|
| 24 seeds, monthly point-in-time PMI, 1-day lag | **b=+0.0013**, z=+0.07, CI [−0.035, +0.038] |

A 1% neighbour move implies +0.1 bp on the candidate. Declared underpowered
*before* running: MDE 0.052 against a 0.02 threshold, because the hypothesis
forces a noisy single stock as the dependent variable and a quiet portfolio as
the regressor. So this bounds a **large** effect and cannot resolve a tradeable
one — "no large effect", never "no effect".

**Later, asked a different question, it became the strongest signal measured
anywhere in this project** (D-136). Not "does co-mention predict returns" — that
is the bounded null above — but "does a correlation that already exists
*persist*". Two effects, opposite signs, which is why an unconditional average
of the column reported 0.4803 and looked like nothing:

| conditional test | result |
|---|---|
| **having** a co-mention edge | 24.1% retained vs 53.7%, **−29.6pp**, CI [−40.2, −17.9] |
| **higher PMI** among edged pairs | **AUC 0.7451**, CI [0.6105, 0.8636] |

Found on the 0.4–0.5 correlation band and then replicated on three bands not
used to find it (0.3–0.4 AUC 0.7870; 0.5–0.6 0.7051; 0.5–1.01 0.6837) — every CI
excludes 0.5, every edge delta negative. The mechanism is coherent and was not
assumed in advance: incidental co-mention marks a correlation the news
manufactured, which decays; high PMI marks companies the press names together
because they are genuinely linked, and that linkage persists.

The catch is coverage, and it is severe: **58 of 6,803 band pairs (0.9%)**. It is
a high-precision flag, not a ranker.

### 3. Supply chain from EDGAR — directed, clean, and almost never present

1,211 customer relations extracted from 5,880 10-K filings, 2018→2026,
point-in-time by filing date. The first **directed** edge in the project: QRVO
names Apple; Apple never names QRVO.

**818 of those 1,211 survive audit** (D-78). Re-reading each stored passage from
the *one sentence that names the counterparty* — rather than from the ±420-char
window, which spans enough text that a competitor list two sentences from a
customer mention scored as a customer — reclassifies 31% of them: 253 name the
party while stating no relation, 66 are competitor lists, 45 acquisitions, 9
explicitly reversed. Two were *South Dakota v. Wayfair*, a sales-tax case read as
a supply relationship. Of the 818 survivors, 685 carry a complete, quotable
disclosure sentence; the other 133 rest on a page header or a financial table.

| test | result |
|---|---|
| Apple's 13 suppliers, 1-day lag | b=+0.0090, z=+0.46, I²=0% |
| pooled across 18 chains | **b=+0.0052**, z=+0.43, pooled estimate **+0.0000**, I²=0% |

⚠️ **Both rows were computed on the pre-audit edge set**, when a third of the
edges were competitor lists and acquisitions. They have not been re-run on the
cleaned 818. The direction of the error is knowable but its size is not: removing
non-supply edges removes noise, so a null computed on contaminated edges stays
null or sharpens — it cannot become a false positive this way. The honest
statement is that these numbers describe a graph that no longer exists (Q-35).

A 1% customer move implies +0.5 bp on its suppliers. Unlike the correlation
nulls, this one is *stable* — no regime dependence, no specification
sensitivity. Still underpowered: MDE 0.0337 against a declared 0.02 threshold.

**As stored, the graph holds 410 edges over 130 distinct pairs** (D-138) — one
document per `(supplier, customer, filing_date)`, so the same relationship
restated across eight years of filings is eight dated edges, which is what makes
the point-in-time traversal work. It was 818 until a deduplication found that
408 were redundant copies of the same triple; the fix had to key on the triple
rather than the pair, because keying on the pair collapses 2018–2026 into one
document and silently breaks every `as_of` query. Where copies disagreed,
non-null `pct_revenue` wins — that rule recovered a real disclosed percentage on
**58 triples** where an arbitrary pick would have kept a null.

For the reach that matters here: only **7 of 6,803** correlation-band pairs have
a supply edge at all. The graph is clean and directed; it is also almost never
the reason two names move together.

### 4. Sector from SEC SIC — the one with coverage

The three definitions above are each either weak or rare. Sector is neither.
`sic` and `sicDescription` come from the same SEC endpoints the filings pipeline
already uses, resolved for **2,180 of 2,183** universe symbols (99.9%).

| grouping | retention lift, band 0.4–0.5 | pair coverage |
|---|---|---|
| same 4-digit SIC | +12.1pp | 19.3% |
| same 3-digit group | +17.5pp | 29.2% |
| **same 2-digit major group** | **+23.1pp**, CI [+20.4, +25.0] | **98.8%** |

Finer groupings are *worse*, which is the useful part: narrow codes split
genuinely related companies apart. Replicated at +28.4pp (0.3–0.4) and +29.6pp
(0.5–1.01). It is also not redundant with the price-derived features — adding it
to the full deterministic set lifts held-out AUC **0.7251 → 0.7455**.

The obvious objection is lookahead: SEC exposes only a company's *current* SIC,
so this joins today's label onto historical pairs. **Measured rather than
assumed** (D-142), using the Financial Statement Data Sets, which publish
`(cik, sic, filed)` per quarter: 96.72% of symbols carry the same 2-digit code
as in 2018, and the bias on the headline number is **−0.38pp** — an order of
magnitude inside the CI, and *negative*. Today's labels understate the effect,
because reclassification mostly breaks a match that genuinely held. No
point-in-time pipeline was built; a commercial sector feed would have cost money
to correct a third of a percentage point in the safe direction.

### A different question, which turned out answerable

Sections 1–3 all ask *does this predict returns*, and the answer is no. Sections
2 and 4 above quietly ask something else — **will a correlation I already
measured still be there later?** — and that one has a signal.

Scored on held-out pairs, 0.4–0.5 band, train/test disjoint:

| ranker | held-out AUC |
|---|---|
| discovery correlation alone | 0.6256 |
| weaker of two halves | 0.6654 |
| median of four quarter-windows | 0.6958 |
| **all deterministic features combined** | **0.7301** |

This is not a return forecast and must not be read as one. It ranks *durability
of a measured relationship*, which is useful for deciding what to put in front of
a human, and says nothing about direction or magnitude of any future move.

### The LLM analysts lose to `sorted()`

Two Claude nodes read the deterministic perspectives and judge the same
replication question. They were built, measured against the number they were
given, and **they lose**:

| arm | held-out AUC |
|---|---|
| Haiku, minimal brief | 0.5588 |
| Haiku, + population base rates | 0.6484 |
| Haiku, + 7 enriched fields (quarters, concentration, liquidity, relatedness) | **0.6216** |
| deterministic median-quarter sort | **0.7043** |

The third row is the finding. Given *more* evidence — the same features that
lift the deterministic ranker to 0.7301 — the model got **worse**, and the
deficit against sorting became statistically established (CI [+0.0383, +0.1455])
where it had previously straddled zero. The information was present; the sort
used it and the model did not. So "give it more context" is not the lever, and
it was the most plausible one.

Two fields in the original brief were measured to carry **zero** information: a
session count that is the same constant on every pair, and a Fisher CI width
that is a deterministic function of the correlation already shown.

The nodes remain wired because the *writing* is worth something — a calibrated
note is readable in a way a column of AUCs is not — but nothing in the verdict
path consults them.

### Why the negative results are the deliverable

Four apparent positives appeared during testing and every one dissolved under a
check the previous one lacked:

| claim | what killed it |
|---|---|
| +115 bp, z=+3.89 | n=122 smoke sample; the full 19,867 reversed the sign |
| +64 bp in one evidence band | the *strongest* band showed +1.66 bp |
| +27 bp, z=+4.53 | a coverage filter I wrote had become a survivorship filter |
| +17.95 bp, z=+2.55 | I²=81%; sign flipped between halves, both "significant" |

The methodology exists because of that. Every test is pre-registered before the
data is fetched, with the decision rule, the economic threshold and the expected
power written down first — see D-59, D-63, D-66, D-73, D-74. Several tests were
declared unpowered *in advance* rather than reported as null afterwards.

### Where it stops

**Three** tests are now at the resolution limit of free data rather than the
limit of effort:

| test | needs | available |
|---|---|---|
| mega-cap correlation (10 bp) | ~21 years of daily history | Alpaca begins 2016 |
| supply chain (0.02 slope) | 2.9× more independent information | 1,978 date clusters is every session the edges span |
| co-mention (0.02 slope) | a different regression orientation | forced by the hypothesis — the candidate must be the dependent variable |

The last one is structural rather than a data shortage. D-74 reached SE 0.0120
by regressing a supplier *portfolio* on a customer *single stock*; the
co-mention hypothesis is that neighbours lead the candidate, so the noisy single
stock has to be the dependent variable and the quiet portfolio the regressor —
the worst arrangement for identification, and no reformulation preserves the
question.

These are stopping conditions, not to-do items. More compute, more sampling and
more patience do not move any of them.

## Graph topology

```
START -(Send, one branch per candidate)-> graph_retriever -+-> (no neighbourhood) -> END
                                                           |
                      leader_state ───────────────┐        |
                      vector_retriever ───────────┴-> context_fusion ──┐
                                                                       |
                      quant_perspective ──> quant_analyst ─────────────┤
                      day_trade_perspective ──> day_trade_analyst ─────┤
                                                                       |
                                          assessor -> review -> publisher -> END
```

Candidates fan out via `Send`, one branch each, into candidate-keyed state
channels. An earlier runner-side loop cross-attributed one candidate's
neighbours to another; keying the channels is what fixed it.

`leader_state` and `vector_retriever` run in parallel and rejoin at
`context_fusion` — the GraphRAG join.

### LangGraph features, and what each cost

| feature | where | outcome |
|---|---|---|
| `Send` map-reduce | `START`, and per-branch routing | fixed the cross-attribution bug |
| `Runtime[Ctx]` + `context_schema` | all nodes | injects `closes`, thresholds, news client |
| `SqliteSaver` / `AsyncSqliteSaver` | `pipeline/runner.py` | every run replayable by `thread_id` (D-45) |
| `RetryPolicy` + `timeout` | `vector_retriever` | news failures halt and resume, not degrade (D-47) |
| `interrupt()` | `review`, a separate pure node | opt-in halt on `contradicted` (D-48) |
| `CachePolicy` | `graph_retriever` | **measured at zero hits** (D-46) — kept, but it earns nothing at this cadence |
| gather nodes | `quant_analyst`, `day_trade_analyst` | one batched LLM call per superstep instead of one per candidate |
| `async` executor throughout | every test, via `conftest.invoke_graph` | an async node cannot run under sync `.invoke()`; the tests had been measuring an executor production never uses (D-131) |

That last row is the point of the table. It stayed in because removing it is a
behaviour change that wants its own test, not because it helps.

## How a verdict is reached

1. **Neighbourhood** — correlate the candidate against ~3,200 symbols over the
   60 sessions ending *before* its date. Point-in-time by construction. The
   signal's own 24 tickers are excluded (D-27), as are 185 leveraged and inverse
   products (D-43) — those track a name mechanically and would corroborate it by
   arithmetic rather than by information.
2. **Shocks** — has any neighbour moved ≥2σ over the last 3 sessions, in units
   of its own trailing volatility?
3. **Independence weighting** — twenty names moving as one bloc are *one*
   observation seen twenty times. Weight is `1/(bloc size)` at ρ≥0.7, so a
   tight sector cluster contributes ~1 unit, not 20. Counting instead of
   weighting overstates the evidence exactly when the graph is working best,
   because the graph selects for correlation (Q-12).
4. **Verdict** — `corroborated` / `contradicted` / `neutral`, plus
   `no_assessment` when no neighbourhood was reachable. Deterministic; no LLM
   in this path.

The quant and day-trade perspectives — including sector match and the
three-state co-mention encoding — are computed for every candidate and surfaced
in the live view, but **`assess()` does not read them**. That is deliberate:
their measured effect is on *multi-year replication of a decade-long
correlation*, while `assess()` asks whether neighbours moved on one date over a
60-session window. Those are different quantities, and wiring the first into the
second as though they were the same is exactly the overclaiming D-34 exists to
prevent.

## The live view

A small local web UI that streams the graph as it executes — not a recording.
Each press of Run starts a real `graph.astream(...)` and pushes every node update
over Server-Sent Events, so the topology lights up in the order the nodes
actually fire and the timings are wall-clock.

```bash
uv run python scripts/serve.py            # http://127.0.0.1:8000
uv run python scripts/serve.py --allow-real
```

Stdlib only — `ThreadingHTTPServer` plus SSE, no web framework and no new
dependencies. It defaults to the committed synthetic universe, so it runs from a
clone with no credentials and no market data. `--allow-real` additionally offers
the local Alpaca bars; without that flag the real source is refused rather than
silently unavailable.

One detail it makes visible: `graph_retriever` fires six times but `leader_state`
only five. SYNF has too little history for a 60-session window, so its branch
routes straight to `END` — the conditional edge shows up in the event stream, not
just the source.

`quant_perspective` reports its deterministic read inline, e.g.
`20 edges, 90% sector match, 2 strong/0 weak co-mentions`. Two states that must
not be confused are rendered differently: **`relatedness not measured`** (no
graph connection) is not the same as a measured zero, and collapsing them is the
mistake that made co-mention look worthless for an hour (D-135 → D-136).

`scripts/capture_trace.py --synthetic` writes the same run to
`docs/trace-synthetic.json` if you want it as a static artefact instead.

## Layout

```
src/lagmatrix/
  shocks.py            standardised moves, shared with a future scanner
  domain/models.py     Bar, Shock, LagEdge, NewsChunk, Candidate, Evidence, Assessment
  graph/
    state.py           candidate-keyed channels + reducers
    context.py         LagMatrixContext — injected config, not globals
    builder.py         build_graph(with_news=, checkpointer=, cache=)
    nodes/
      graph_retriever.py   point-in-time neighbourhood
      leader_state.py      have the neighbours moved?
      vector_retriever.py  co-mention news (async, Alpaca News API)
      context_fusion.py    independence-weighted evidence
      assessor.py          evidence -> verdict (deterministic)
      review.py            interrupt() gate on contradicted verdicts
      publisher.py         assessments -> stdout
  adapters/
    candidates.py      ExternalSignals — the extension seam
    market.py          Alpaca bars
  pipeline/runner.py   run() / run_sync(), checkpointing, allowlisted serde
```

**Not implemented, and honest about it:** `adapters/arango.py`,
`adapters/vector.py`, `adapters/llm.py`, `logging.py`, `config.py`,
`__main__.py`, and `MarketScan` in `candidates.py` are stubs raising
`NotImplementedError`. `graph/nodes/ingest.py` is dead — `Send` fan-out from
`START` replaced it and it was left in place rather than deleted mid-refactor.

ArangoDB is a *tentative* decision (D-13), gated on Q-09 and deliberately not
built (D-34): at 98 candidates a pandas correlation over the universe is
adequate, and the graph store earns its place only under market-scan volumes.
The README used to claim it was in use. It was not.

**Extending to market scanning** is a one-class change — implement `MarketScan`
behind `adapters/candidates.py`. The graph is mode-agnostic; producing
candidates is where the mode lives, so no node branches. One caveat first: Q-26
records a latent self-correlation bug that production avoids only because D-27
excludes the alert tickers. Under `MarketScan` every candidate would contradict
itself.

## Setup

```bash
uv sync
cp .env.example .env   # ALPACA_API_KEY / ALPACA_SECRET_KEY, for news and bars
uv run pytest
uv run ruff check
```

CI runs the suite on **x86_64 and aarch64** (D-53). The synthetic golden file
encodes floating-point reduction order, so "passes on my machine" was not good
enough — it is regenerated and diffed byte-for-byte on both architectures.

## The baseline guard

`scripts/check_baseline.py` regenerates the pipeline's verdict for every
candidate and diffs it against a frozen table. It caught the `--news` regression
in PHASE-2, which every unit test had missed. Two modes.

**Synthetic — runs from a clone, and runs in the suite.**

```bash
uv run pytest tests/test_synthetic_baseline.py    # ~1s
uv run python scripts/check_baseline.py --synthetic
```

`tests/fixtures/` holds a fabricated universe: 166 invented tickers, 100
sessions, six candidates each built to land on a distinct branch — corroborated
on an up signal and on a down one, contradicted, neutral by tie, neutral by
insufficient evidence, and `no_assessment`. Nothing in it is a real price or a
real signal.

Constructing the branches was the point. The real data never once produced a
`w_pro == w_con` tie, so that branch went unguarded for the whole refactor. A
second test asserts all four verdict values remain present, so the guard cannot
decay into checking one path. `scripts/make_synthetic.py` regenerates it.

It guards *pipeline behaviour*, not market truth: a change that alters real
verdicts while leaving synthetic ones intact would pass.

**Real — needs data this repo does not carry.**

```bash
uv run python scripts/check_baseline.py           # ~40s
```

Reads `data/bars.parquet` and `data/fires.csv`, diffs against
`data/baseline.csv`. Absent them it exits non-zero rather than falling back —
a silent fallback would let the guard report "unchanged" while the real inputs
were missing.

## Data

Three stores, none of them committed.

**Local parquet** — analytical caches, regenerated by script:

| file | span | contents |
|---|---|---|
| `data/bars.parquet` | 159 sessions | OHLCV + vwap + dollar_vol, 3,204 symbols |
| `data/bars-10y.parquet` | 2016→2026, 2,514 sessions | close + volume, 2,183 symbols, 4.7M rows |
| `data/minute*.parquet` | 48 alert dates | 1-minute closes, ~1.1M rows |

**Postgres** (`lagmatrix` schema, inside the existing `orchestrator` database —
namespaced away from the trading tables, but joinable to `audit_log`):

| table | rows | purpose |
|---|---|---|
| `news_article` / `news_symbol` | 229,737 / 932,545 | Benzinga 2014→2026, 248 MB |
| `news_comention` | *view* | undirected co-mention pairs |
| `filing_mention` | 1,211 → **818** | 10-K customer disclosures after audit, 2018→2026 |
| `supply_edge` | *view* | directed supplier → customer |

Both graph tables are exposed as **views, never materialised**, so every caller
must supply its own `created_at < as_of` or `filing_date < as_of` bound.
Materialising them would bake in a single as-of date and make look-ahead a
matter of forgetting to filter rather than an impossibility.

`filing_mention` stores the **passage verbatim** alongside a `relation` label.
The passage is the durable artefact; the label is disposable. That design paid
off directly: D-78's reclassification re-read all 1,211 passages and rewrote
every label without re-crawling EDGAR, turning one hardcoded `'customer'` into
six labels (`customer`, `reversed`, `competitor`, `corporate_action`, `unstated`,
`unnamed`) and marking them `confidence='sentence'`. Every edge stays auditable
against the filing that produced it — which is exactly how the 31% was found.

## What is not in this repo, and why

| withheld | reason |
|---|---|
| `data/bars.parquet` | Alpaca bars — vendor data, not ours to redistribute |
| `data/fires.csv` | the upstream signal feed |
| `data/baseline.csv` | tickers, dates and directions actually traded |

`data/excluded-etfs.csv` **is** committed: product names only, no prices and no
signal content, and `pipeline/runner.py` loads it at startup. Reproducing the
rest needs your own Alpaca credentials and your own signal source —
`scripts/fetch_bars.py`, then `scripts/capture_baseline.py`.

## Scripts

| | |
|---|---|
| `serve.py` | local web UI streaming a live run over SSE (`--port`, `--allow-real`) |
| `run_pipeline.py` | run the graph over cached bars (`--limit`, `--news`, `--thread-id`) |
| `replay.py` | print a run's checkpoint history by `thread_id` |
| `capture_trace.py` | stream a run to JSON for the web view (`--synthetic`) |
| `make_synthetic.py` | regenerate the synthetic universe |
| `capture_baseline.py` / `check_baseline.py` | freeze and diff the verdict table |
| `fetch_bars.py` / `fetch_history.py` / `fetch_minute.py` | daily, decade and minute panels |
| `extract_fires.py` | pull the signal feed from the upstream `audit_log` |
| `build_exclusions.py` | fund classifier (`--leveraged-only` for pre-D-62 behaviour) |
| `load_news.py` | backfill Benzinga into Postgres, idempotent by 90-day window |
| `load_edgar.py` | crawl 10-Ks for directed customer edges, concurrent + throttled |
| `experiment.py` / `experiment2.py` | the first two pre-registrations (D-31, D-33) |
| `experiment3.py` | the shipped feature on synthetic candidates (D-63, D-64) |
| `experiment4.py` / `experiment5.py` | supply-chain lead-lag, one chain and pooled (D-73, D-74) |
| `horizon_ladder.py` | decay profile with block-clustered errors (D-66) |
| `intraday_lag.py` | minute-resolution cross-correlation (D-59, D-60) |
| `seed_arango.py` | bootstrap for the graph store that is not yet built |
| `load_arango.py` | load equity/supply/co-mention into ArangoDB; merges, never replaces (D-143) |
| `load_sectors.py` | upsert SEC SIC onto `equity` vertices, re-runnable (D-137) |
| `reconcile_supply_edges.py` | one-time `supplies_to` dedupe by dated triple (D-138, `--apply`) |
| `measure_pmi_threshold.py` | the weak/strong co-mention cutoff, from a band D-136 did not use |
| `measure_sic_drift.py` | bounds the SIC lookahead against point-in-time filings (D-142, `--quarter`) |
| `experiment_quant_perspective.py` | arm A: does split-half agreement predict replication |
| `experiment_arm_b.py` / `_b2.py` / `_b3.py` | arms B/B2/B3: the LLM against the sort (D-133, D-134, D-135) |

## Research & decisions

`docs/spikes/overall.md` is the running log: 75 decisions, 34 questions, 14 spike
write-ups. Every decision names the alternative that lost and carries an
`Outcome` field that stays `pending` until the decision has actually been
exercised — including the ones that turned out wrong. Read it before changing
direction; append to it after.
