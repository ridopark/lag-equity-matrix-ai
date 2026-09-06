# 01 — ArangoDB as the GraphRAG substrate, and who has built this already

**Question:** Can ArangoDB carry both halves of the GraphRAG design (Q-02), and
does prior art on GitHub already cover lead-lag + GraphRAG for equities?
**Date:** 2026-09-03
**Status:** done (desk research only — nothing benchmarked)

## What we tried

Desk research, no code. Three lines of enquiry:

1. ArangoDB's native vector-search capability and its LangChain integration —
   specifically whether it can replace a separate vector store.
2. GitHub search for prior art across four framings: lead-lag correlation,
   GraphRAG + finance, LangGraph + trading, ArangoDB + LangGraph.
3. The academic standing of the lead-lag effect itself — is the signal real,
   and at what horizon.

Deliberately **not** tried: any benchmark, any local ArangoDB instance, any
latency measurement. Everything below is documentation and source reading.

## What we measured

### ArangoDB can host the vector half

- Vector indexes are **Faiss-backed**, exposed through `APPROX_NEAR_COSINE` /
  `APPROX_NEAR_L2`, with `cosine` and `l2` metrics. Index factory strings like
  `IVF100_HNSW10,Flat` are supported — the base index must be IVF, so this is
  not standalone HNSW.
- Requires a startup flag: `--vector-index`, named `--experimental-vector-index`
  in v3.12.4 / v3.12.5.
- Approximate search requires **ArangoDB >= 3.12.4**; exact search works on any
  version. Up to v3.12.8 vectors had to be loaded *before* index creation; from
  v3.12.9 the index can be created first.
- `langchain-arangodb` **v2.1.0** ships `ArangoVector` (a real LangChain
  `VectorStore`), plus `ArangoGraph` and `ArangoChatMessageHistory`. It supports
  MMR and **hybrid search via Reciprocal Rank Fusion** over vector + keyword.
  Officially maintained at `arangoml/langchain-arangodb`.
- ArangoDB also ships a turn-key **GraphRAG** suite that extracts entities from
  raw text into a KG. Noted but **not** what this project needs — our topology is
  structured and computed, not extracted from prose.

### The pre-filtering question is unresolved and directly on our critical path

Our retrieval is not pure similarity. It is *similarity **and** `symbol IN
(laggers)` **and** recent* — so whether the filter runs before or after the ANN
search decides whether we get 20 relevant chunks or 200 irrelevant ones.

The sources contradict each other:

| Source | Claim |
|---|---|
| arangodb/arangodb issue #21690 (opened 2025-03-26) | Cannot pre-filter; post-filter only. "I can't isolate workspaces from each other." Labeled `2 SolvedResolution`, but the thread shows no maintainer resolution. |
| Docs (via search) | Filtering between `FOR` and `SORT` **is** applied during the index lookup, handled by the `use-vector-index` optimizer rule, **in v3.12.6**. |
| 3.13 docs (still in development) | "You cannot have any `FILTER` operation between `FOR` and `LIMIT` for pre-filtering." |
| ArangoDB blog | Demonstrates combining vector search with graph traversal and calls it "HybridGraphRAG" — no limitation mentioned. |

Documented workaround if pre-filtering is unavailable: a `LET` subquery to
pre-identify the candidate set, then vector search over that; or simply raise the
`LIMIT` and post-filter. Both are worse than a real pre-filter.

`docs.arango.ai` returns **403 to automated fetches**, so the authoritative
current page could not be read directly. This must be settled hands-on.

### Prior art: the specific niche is open

GitHub searches returned:

- **lead-lag + GraphRAG + ArangoDB: zero repositories.** Nothing combines them.
- `graphrag finance knowledge graph trading agent` → 0. `supply chain knowledge
  graph stock prediction neo4j` → 0. `financial knowledge graph RAG equity
  research LLM` → 0.
- **ArangoDB + LangGraph: one repo**, `JoecheleLim/Exploring-Agentic-App-with-
  ArangoDB-NetworkX-cuGraph-and-LangGraph` (Mar 2025, Amazon product reviews,
  unmaintained). Not finance.
- **Lead-lag repos exist but are plain quant** — `tommydj9/Lead_lag_stock`
  (correlation study, no graph, no LLM), `Key07211/tech-finance-analytics-
  dashboard` (dashboard across 35 stocks). Neither has a knowledge graph or an
  agent.
- **The crowded space is single-ticker multi-agent debate.**
  `TauricResearch/TradingAgents` (~99.8k stars, arXiv 2412.20138) is the
  reference implementation: bull/bear researcher agents, risk team, trader,
  built on LangGraph. Its many forks (TradingAgents-CN-lite, TradeHive,
  TradingAgents-Pro, OpenTrade.ai, Hermes-LangGraph-Quant-Signal-Pipeline) all
  reproduce the same shape.

  **TradingAgents analyzes one ticker at a time.** It has no cross-asset
  topology and no notion of a shock propagating from one company to another.
  That gap is precisely this project's thesis.

### The academic signal is real — but the horizon may not match our design

The lead-lag / momentum-spillover literature is solid and long-standing:
customer-supplier links produce predictable returns because information diffuses
slowly across economically linked firms (investor inattention and limited
processing capacity). "A frog in every pan" (JFE) refines it: the effect appears
only when the leader's information is *continuous* — discrete information is
priced into the lagger immediately. Network Momentum (arXiv 2308.11294) extends
spillover across asset classes.

**The mismatch:** this literature documents predictability at **daily-to-monthly**
horizons. The current design assumes minute-scale leads (`lag_minutes`,
`shock_window_minutes: 15`). No source found supports a 15-minute exploitable
supply-chain lead in liquid US large caps.

## Conclusion

Two things change, one thing gets flagged.

1. **Drop Qdrant.** ArangoDB covers both halves — graph traversal and vector
   search — through one service, one connection, one transaction, with an
   official LangChain vector store. For a project whose whole point is fusing
   structured and unstructured retrieval, doing both in one query engine is a
   better story, not a compromise. → D-13.

2. **The pre-filter question moves to the critical path.** It is the one thing
   that could make ArangoDB the wrong choice, sources disagree, and the docs are
   unreadable to automated fetching. Settle it with a local instance before
   writing `adapters/vector.py`. → Q-09.

3. **The horizon assumption is the project's biggest unvalidated risk** — bigger
   than any infrastructure choice. The evidence base is for daily-to-monthly
   effects; the design is built for minutes. If minute-scale leads do not exist
   in liquid names, the honest fix is to retarget the horizon (or the universe
   toward less-liquid, less-covered names), not to tune the pipeline. → Q-10.

Prior art confirms the concept is differentiated: the multi-agent trading space
is saturated with single-ticker debate frameworks, and none of them model shock
propagation across a market topology.
