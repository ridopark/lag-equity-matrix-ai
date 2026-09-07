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

**The premise does not hold.** Three different ways of defining "related
company" were built and tested. None predicts returns at the horizons this
signal feed trades.

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

### 2. News co-mention — tested, bounded

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

### 3. Supply chain from EDGAR — directed, and the cleanest null

1,211 customer edges extracted from 5,880 10-K filings, 2018→2026,
point-in-time by filing date. The first **directed** edge in the project: QRVO
names Apple; Apple never names QRVO.

| test | result |
|---|---|
| Apple's 13 suppliers, 1-day lag | b=+0.0090, z=+0.46, I²=0% |
| pooled across 18 chains | **b=+0.0052**, z=+0.43, pooled estimate **+0.0000**, I²=0% |

A 1% customer move implies +0.5 bp on its suppliers. Unlike the correlation
nulls, this one is *stable* — no regime dependence, no specification
sensitivity. Still underpowered: MDE 0.0337 against a declared 0.02 threshold.

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
                                       leader_state -------+
                                       vector_retriever ---+-> context_fusion
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
| `filing_mention` | 1,211 | 10-K customer disclosures, 2018→2026 |
| `supply_edge` | *view* | directed supplier → customer |

Both graph tables are exposed as **views, never materialised**, so every caller
must supply its own `created_at < as_of` or `filing_date < as_of` bound.
Materialising them would bake in a single as-of date and make look-ahead a
matter of forgetting to filter rather than an impossibility.

`filing_mention` stores the **passage verbatim** alongside a `relation` label
marked `confidence='heuristic'`. The passage is the durable artefact; the label
is disposable. Re-labelling with an LLM never re-crawls EDGAR, and every edge
stays auditable against the filing that produced it.

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

## Research & decisions

`docs/spikes/overall.md` is the running log: 75 decisions, 34 questions, 14 spike
write-ups. Every decision names the alternative that lost and carries an
`Outcome` field that stays `pending` until the decision has actually been
exercised — including the ones that turned out wrong. Read it before changing
direction; append to it after.
