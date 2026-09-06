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

Over 98 real signals across 24 tickers:

| verdict | n |
|---|---|
| neutral | 74 |
| contradicted | 12 |
| corroborated | 9 |
| no_assessment | 3 |

Two pre-registered tests of the core premise both came back **inconclusive** —
D-31 at n=62, D-33 at n=67. Underpowered, not evidence of absence: at ~25 per
bucket the minimum detectable effect was ~28pp, and both were run knowing that
(D-30), because waiting two years for power is not a plan. The feed yields ~30
signals/month, which puts a 15pp-detectable test around May 2027 and a
10pp one in 2028.

That is the honest state of the hypothesis. `docs/spikes/overall.md` carries the
working, including the design errors caught after the fact.

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
| `fetch_bars.py` / `extract_fires.py` | build the local inputs |
| `build_exclusions.py` | leveraged/inverse classifier (D-43) |
| `experiment.py` / `experiment2.py` | the two pre-registered tests (D-31, D-33) |
| `seed_arango.py` | bootstrap for the graph store that is not yet built |

## Research & decisions

`docs/spikes/overall.md` is the running log: 53 decisions, 27 questions, 12 spike
write-ups. Every decision names the alternative that lost and carries an
`Outcome` field that stays `pending` until the decision has actually been
exercised — including the ones that turned out wrong. Read it before changing
direction; append to it after.
