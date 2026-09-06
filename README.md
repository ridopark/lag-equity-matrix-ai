# LagMatrix

Multi-agent financial signal pipeline built on **LangGraph** and **GraphRAG**.

LagMatrix asks whether a market's **topology corroborates a trading signal you
already have.** Given a candidate name, it reconstructs that name's
leader/lagger neighbourhood as of that date, checks whether the leaders have
already moved while the candidate has not yet repriced, pulls the news that
links them, and returns a two-sided assessment — including the case *against*.

It does not originate buy/sell decisions (D-18). Both storage roles — the
topology graph and the news vectors — live in ArangoDB (D-13), and every input
comes from the Alpaca API (D-16).

Runs on a **daily cadence over a 1-10 day horizon**: the documented lead-lag
effect is information diffusion at daily-to-monthly scale, not an intraday one
(D-15).

**Extending to market scanning** is a one-class change. See
`adapters/candidates.py`.

> Status: skeleton. Every module is a documented stub raising `NotImplementedError`.

## Layout

```
src/lagmatrix/
  config.py            Settings (env / .env), single source of external config
  logging.py           structured logging setup
  domain/models.py     Bar, Shock, LagEdge, NewsChunk, Candidate, Evidence, Assessment
  shocks.py            shock detection, shared by the node and the future scanner
  graph/
    state.py           LagMatrixState — the state threaded through the graph
    builder.py         StateGraph wiring + conditional routing
    nodes/
      ingest.py            CandidateSource -> candidates
      graph_retriever.py   candidate -> point-in-time neighbourhood (structured)
      leader_state.py      neighbourhood -> have the leaders moved?
      vector_retriever.py  neighbourhood -> co-mention news        (unstructured)
      context_fusion.py    observations -> independence-weighted evidence
      assessor.py          evidence -> verdict + odds adjustment   (LLM)
      publisher.py         assessments -> downstream
  adapters/
    candidates.py      ExternalSignals | MarketScan  <- the extension seam
    arango.py          topology graph traversal (AQL)
    vector.py          news/earnings semantic search
    market.py          end-of-day bar fetch (Alpaca)
    llm.py             Claude client
  pipeline/runner.py   daily run driving the compiled graph
tests/                 fixtures fake all external systems
scripts/seed_arango.py collection + starter-topology bootstrap
docs/architecture.md
```

## Graph topology

```
ingest -> graph_retriever -+-> (no neighbourhood) -> END
                           |
                           +-> leader_state -----+
                               vector_retriever -+-> context_fusion
                                                       |
                                                       v
                                                   assessor -> publisher -> END
```

`leader_state` and `vector_retriever` fan out in parallel and rejoin at
`context_fusion` — this is the GraphRAG join.

The graph is **mode-agnostic**: scanning the market swaps the CandidateSource
behind `ingest`, not the wiring. Sweeping the universe for shocks and traversing
to laggers happens while *producing* candidates, so no node needs to branch.

## Setup

```bash
uv sync
cp .env.example .env   # fill in credentials
uv run pytest
uv run ruff check
```

## Research & decisions

`docs/spikes/overall.md` is the running log — decision log, open questions, and an
index of spike write-ups. Read it before changing direction; append to it after.
