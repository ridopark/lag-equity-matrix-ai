# PLAN-2026-09-07-graphrag-showcase

**Status: done.** Both adapters are real (`adapters/arango.py`, `adapters/vector.py`), wired as retrieval nodes, and visible in the live UI.

**Goal:** Turn `adapters/arango.py` and `adapters/vector.py` from two 21-line
stubs into a real ArangoDB-backed multi-hop directed point-in-time graph
traversal and a real CPU-embedded semantic search over the news corpus, wire
both into the LangGraph pipeline as the retrieval nodes they were always meant
to be, and make both visible and demoable in the live web UI — closing out the
project's original brief (a LangGraph + graph-DB + vector-DB showcase) now that
the research thread that displaced it is closed.

**Decisions this depends on:** D-02 / D-13 (ArangoDB for both the topology and
the vectors; Qdrant dropped), D-16 (edges are derived — partially reversed by
this plan, see PHASE-0), D-23 (`CandidateSource` is the only mode seam — this
plan touches no mode branching), D-27 (neighbourhoods from the wide universe,
untouched), D-34 (deferred building the graph store — this plan is the
reversal), D-35 (`Runtime`-injected dependencies, `None` disables — this plan's
integration pattern copies it), D-36 (TDD is binding), D-67/D-69 (news corpus
in Postgres, 229,737 articles), D-70 (co-mention needs breadth<=8 + PMI — not
used by this plan directly, but the breadth filter is reused for the embedding
subset), D-72 (directed `filing_mention`/`supply_edge`, supplier→customer,
2018-10-19→2026-08-20), D-73/D-74/D-75 (the three edge types are research
nulls — closing that thread is *why* this plan exists, not a reason to avoid
building the edges; D-34's "no ArangoDB yet" was conditioned on exactly this
data not existing).

**Open questions that could invalidate it:** Q-09 (can ArangoDB pre-filter a
vector search by symbol/recency, or only post-filter) — de-escalated by D-15 to
a correctness question, not a latency one; PHASE-6 probes the installed
`langchain-arangodb` directly rather than assuming, per this project's own
D-35/D-44 lesson. None of Q-05/Q-06/Q-07/Q-12/Q-19/Q-25/Q-28/Q-30/Q-33 are
touched — this plan does not reopen the corroboration-layer research.

**Explicitly out of scope:** no change to `context_fusion.py`'s evidence
weighting (news stays context-only, `weight=0.0`, per D-34's "no calibrated
confidence" and Q-12's discounting being unresolved); no new predictive claim
about any edge type (D-73/D-74/D-75 stand); no `MarketScan` implementation
(D-23's stub is untouched).

**Verified against real data before writing this plan** (all read-only, over
SSH to the homelab Postgres, 2026-09-07): `supply_edge` is a directed view
(`supplier`, `customer`, `pct_revenue`, `filing_date`); a genuine 2-hop chain
exists and is checkable — `GEN/LITE/DELL → AVGO → AAPL` and
`QCOM/MXL/ADI/MTSI/COHR → QRVO → AAPL` (17 tier-2 rows via AAPL's 13 direct
suppliers); AAPL and AVGO are both in the 24-ticker alert universe
(`data/fires.csv`), so the demo's real candidates hit real chains, not just
synthetic ones. Restricting the news corpus by symbol tag does **not** bound
volume (207,642 of 208,161 breadth<=8 articles already mention one of the
24 alert tickers — mega-caps saturate the corpus); the only axis that bounds it
is recency. `arangodb/arangodb:3.12.4.3` (>= D-13's 3.12.4 floor) ships both
`linux/amd64` and `linux/arm64` manifests, confirmed against Docker Hub's
registry API — a CI service container is viable on both matrix legs.
`langchain-arangodb` (2.1.0), `sentence-transformers` (6.0.1) and
`python-arango` (8.3.5) are all on PyPI. WSL has no docker daemon (confirmed:
`docker info` fails, the `docker` binary on PATH is Docker Desktop's Windows
interop shim); systemd **is** available in this WSL distro, so a native apt
install is viable where docker is not. The dev machine itself has 31 GB RAM /
24 cores — the "4 GB homelab headroom" constraint does not apply if nothing is
deployed to the homelab, which is what this plan recommends.

---

## Success criteria

The plan is done when **all** of the following hold, verified by the exact
commands named in each phase — nothing here may be relaxed at execution time.

1. `uv run pytest -q` passes with the current 44 tests plus every test this
   plan adds — none deleted, none weakened.
2. `uv run ruff check` passes.
3. `uv run python scripts/check_baseline.py --synthetic` and (if
   `data/bars.parquet` + `data/fires.csv` are present locally)
   `uv run python scripts/check_baseline.py` both report unchanged — the new
   retrieval paths are additive and opt-in via `LagMatrixContext`, defaulting
   to `None`, so the existing goldens are untouched unless explicitly enabled.
4. `adapters/arango.py` performs a real multi-hop, directed, point-in-time AQL
   traversal against a real ArangoDB instance, verified by an executed test —
   not a mock — both locally (native install) and in CI (service container,
   both `x86_64` and `aarch64`).
5. `adapters/vector.py` performs real cosine similarity search over real
   sentence-transformer embeddings of real news text, verified the same way.
6. `config.max_lag_hops` (carried since the project's first commit, never
   read) is read by `ArangoTopology`'s traversal and changes its result at
   `max_lag_hops=1` vs `max_lag_hops=2` on the real AAPL/AVGO/QRVO chain.
7. The live web UI (`scripts/serve.py`) can demonstrate both retrieval paths
   from a fresh clone with **no Alpaca credentials and no `data/` files** —
   the EDGAR-sourced graph and the Postgres-sourced news are not
   Alpaca-restricted vendor data, unlike `data/bars.parquet`/`data/fires.csv`.
8. `docs/spikes/overall.md` carries a decision superseding D-34's "no ArangoDB
   yet" constraint, with an `Outcome` filled in from this plan's own execution,
   not left `pending`.

---

## Baseline (measured 2026-09-07, before any change)

- `git rev-parse HEAD` → `030e988b93d0f0e6e238d8433babcb6ded7ca19e`
- `uv run pytest -q` → **44 passed**
- `uv run ruff check` → **All checks passed!**
- `uv run mypy` → **30 pre-existing errors in 15 files** — not a CI gate today
  (`ci.yml` runs `ruff` and `pytest`, not `mypy`); this plan does not add a
  new mypy gate and does not need to fix these to stay surgical.
- `data/fires.csv` → 102 data rows, 24 distinct tickers (includes AAPL, AVGO).
- `data/baseline.csv` → 102 rows; `tests/fixtures/synthetic-baseline.csv` → 6
  rows.
- `src/lagmatrix/adapters/arango.py` / `adapters/vector.py` → 21 / 14 lines,
  two `NotImplementedError`s each, imported by nothing (`grep -rn` finds no
  caller outside their own module and `scripts/seed_arango.py`, which is a
  docstring-only stub).
- `scripts/serve.py` calls `build_graph(with_news=False)` unconditionally —
  the live UI does not exercise `vector_retriever` at all today, real or fake.
- `pyproject.toml` lists `qdrant-client>=1.12` and `config.py` carries
  `qdrant_url`/`qdrant_collection` — both dead since D-13 (2026-09-03),
  never removed.

---

## PHASE-0 — Decisions, and confirming the environment this plan assumes

**Routing:** no code, no tests. Pure decision-log entries (`spike-log` skill)
plus read-only verification commands. Exempt from TDD as pure documentation.

- TASK-0.1 (spike-log): Log a decision **superseding D-34's constraint 2** ("No
  ArangoDB yet"). State plainly: D-34's condition for revisiting was "it
  becomes right when edges get expensive to recompute or when co-mention/
  semantic edges arrive" — D-67/D-70/D-72 are exactly that arrival. Reference
  this plan.
- TASK-0.2 (spike-log): Log the deployment-topology decision: **one ArangoDB
  instance, native (non-Docker) install on the dev machine for local
  work/demo; a `services:` Docker container in CI for both matrix legs**;
  serves both the graph and the vector role (reaffirms D-13; Qdrant removal is
  PHASE-1). State the reasoning: the homelab's 4 GB headroom and ssh+kubectl-
  only access are real constraints *of the homelab*, not of the dev machine,
  and this plan deploys to neither — WSL's missing docker daemon is the
  constraint that actually binds locally, and a native package sidesteps it.
  **Flag explicitly for the owner to confirm or override**: this rejects
  deploying to the homelab k3s cluster. If the owner wants it there instead
  (e.g. so the showcase runs without the dev machine), that changes PHASE-2
  through PHASE-9 materially — say so before proceeding past PHASE-1.
- TASK-0.3 (spike-log): Log the embedding-subset decision: articles with
  breadth <= 8 (D-70), `created_at >= 2025-01-01`, tagging at least one of the
  24 alert-universe tickers — **measured at 47,763 articles** (vs. 208,161 for
  the unfiltered breadth<=8 corpus; symbol-tag filtering alone does not bound
  volume, recency does). **Flag explicitly for the owner**: this bounds the
  demo to "current era" news and excludes 2014-2024; widening it is a one-line
  date-constant change with no design impact, but it is a real scope choice,
  not a default that can be silently assumed correct.
- TASK-0.4 (infra, no behaviour change): Add the ArangoDB apt repository and
  install `arangodb3=3.12.4.3-1` natively (systemd-managed) on the dev
  machine. **Verification:** `curl -s http://localhost:8529/_api/version`
  returns JSON containing `"version"`.
- TASK-0.5 (infra, no behaviour change): Confirm `arangodb/arangodb:3.12.4.3`
  is usable as a GitHub Actions service container on `ubuntu-24.04-arm`
  specifically (this plan verified the image manifest lists `arm64`, but not
  that the runner successfully starts it — that needs an actual CI run).
  **Verification:** a `workflow_dispatch` run of a throwaway CI job that
  starts the service and health-checks it, on both matrix legs.
  **Halt condition:** if the `aarch64` runner cannot start the container
  (e.g. a libc or memory issue distinct from the manifest existing), stop and
  raise it as a new open question — do not silently drop `aarch64` coverage
  from the two new phases that need it (PHASE-3, PHASE-6) without saying so.

## PHASE-1 — Dependency and config cleanup

**Routing:** config/infra, no behaviour change — exempt per CLAUDE.md.
**Completion criterion:** `uv run pytest -q` still reports 44 passed; `uv run
ruff check` still passes; `grep -rn qdrant src/lagmatrix pyproject.toml
.env.example` returns nothing.

- TASK-1.1: Remove `qdrant-client` from `pyproject.toml`; add
  `langchain-arangodb>=2.1`, `sentence-transformers>=6.0`, `torch` (CPU;
  `sentence-transformers` pulls it transitively — pin nothing extra unless
  `uv sync` resolves a GPU build, which is unwanted here).
- TASK-1.2: In `config.py`, remove `qdrant_url`/`qdrant_collection`; add
  `arango_vector_collection: str = "lagmatrix_news"` and
  `embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"`. Leave
  `arango_url`/`arango_db`/`arango_user`/`arango_password` as-is (D-13 already
  named them for both roles).
- TASK-1.3: Mirror the same removal/addition in `.env.example`.
- TASK-1.4: In `LagMatrixContext` (`graph/context.py`), add two new
  `Runtime`-injected, `None`-defaulting fields: `arango_topology: object |
  None = None` and `vector_index: object | None = None`, plus
  `max_lag_hops: int = 2` (mirrors `Settings.max_lag_hops`, finally plumbed
  somewhere a node can read it). `None` disables the new path exactly as
  `news_client: object | None = None` already does — this is what keeps
  PHASE-4/PHASE-7 additive rather than a breaking change.

**Halt condition:** if `uv sync` cannot resolve `sentence-transformers` +
`torch` (CPU) without a GPU wheel pulling in multi-GB CUDA dependencies, stop
and raise it — the fix (a CPU-only torch index URL) is a `pyproject.toml`
detail worth getting right rather than downloading gigabytes silently.

## PHASE-2 — Sync the supply-chain graph from Postgres into ArangoDB

**Routing:** one-shot script under `scripts/` — exempt per CLAUDE.md.
**Completion criterion:** `uv run python scripts/seed_arango.py --graph`
followed by an AQL count query returns exactly the Postgres row counts it read
(falsifiable: the script prints both counts and asserts equality itself,
non-zero exit on mismatch).

- TASK-2.1: Flesh out `scripts/seed_arango.py` (currently a docstring-only
  stub) to: (a) create an ArangoDB graph named `lagmatrix` with an `equities`
  vertex collection and a `supplies_to` edge collection; (b) connect over SSH
  + `kubectl exec` + `psql` exactly as `load_news.py`/`load_edgar.py` already
  do (5432 has no external route — this is not a new constraint, it is the
  one every prior Postgres-reading script in this repo already works around);
  (c) read `lagmatrix.supply_edge` (1,211 rows) and upsert one edge document
  per row: `_from="equities/<supplier>"`, `_to="equities/<customer>"`,
  `pct_revenue`, `filing_date`, `confidence`. Idempotent by
  `(supplier, customer, filing_date)` unique key, matching `load_news.py`'s
  idempotency convention.
- TASK-2.2: Vertex documents are created lazily (upsert-if-absent) from
  whichever symbols appear in the edges — no separate equities master list
  exists or is needed for this plan's scope.

**Halt condition:** if SSH/kubectl access is not available to whoever executes
this plan (it was verified from this planning session, but plan execution may
happen in a different environment), stop — do not fall back to fabricating
graph data.

## PHASE-3 — `ArangoTopology`: real multi-hop, directed, point-in-time traversal

**Routing:** `tdd-red` → `tdd-green` → `tdd-refactor`. This is a genuine new
behaviour (the method currently raises `NotImplementedError`), so full TDD
applies, not an exemption.

**Design note, stated plainly because it deviates from the original stub:**
`laggers_of(leader, max_hops)` was declared under D-02 (2026-09-03) for the
still-unbuilt `MarketScan` mode (D-23: "traverses each shocked leader to its
laggers, emitting one Candidate per lagger") — nothing calls it today. What
`graph_retriever` (PHASE-4) actually needs is the **other direction**: given a
candidate (a potential lagger), find the things that lead it, so its own
supply-chain edges must be walked customer-ward, not supplier-ward. Rather
than overload one method with two directions, this plan adds
**`leaders_of(lagger, max_hops, as_of)`**, leaves `laggers_of` exactly as
declared (still `NotImplementedError`, still reserved for `MarketScan`, zero
lines changed), and this deviation itself is worth one spike-log line since it
extends D-02's adapter contract rather than merely implementing it.

- TASK-3.1 (test, `tdd-red`): Add `tests/test_arango_topology.py` with a
  module-scoped fixture that connects to `LAGMATRIX_ARANGO_URL`
  (default `http://localhost:8529`) and calls `pytest.skip(...)` if
  unreachable — mirrors this repo's existing pattern of never silently
  degrading (D-38), just skipping cleanly when the optional dependency isn't
  running. Seed a small fixture graph reproducing the **real, verified** chain:
  `GEN/LITE/DELL -> AVGO -> AAPL` and `QCOM/MXL/ADI/MTSI/COHR -> QRVO -> AAPL`,
  with real `filing_date`s from the verified query. Tests, each stating what
  would make it fail:
  - `leaders_of("GEN", max_hops=1, as_of=<after AVGO->AAPL's filing_date>)`
    returns `AVGO` only. Falsifies if it returns nothing (wrong direction) or
    also returns `AAPL` (hop limit not enforced).
  - `leaders_of("GEN", max_hops=2, as_of=...)` returns both `AVGO` and `AAPL`.
    Falsifies if `AAPL` is missing (hop 2 not reached) — this is the test that
    proves `max_lag_hops` finally does something.
  - `leaders_of("GEN", max_hops=2, as_of=<before AVGO->AAPL's filing_date>)`
    returns `AVGO` only, not `AAPL` — point-in-time: a filing that had not
    happened yet must not be traversable. Falsifies if `AAPL` leaks through
    regardless of `as_of` (no filing_date filter in the AQL).
  - `leaders_of("NONEXISTENT", max_hops=2, as_of=...)` returns `[]`, not an
    error.
> **CORRECTED 2026-09-07 (D-79) — this phase had the candidate on the wrong end.**
> The prose above assumes "a candidate is a potential lagger". That is false for
> this candidate set. D-73 fixes the predictive direction as customer → supplier,
> and D-72 measured that **the alert tickers are all at the customer end** ("only
> 8 of the 24 alert tickers are reachable as customers"). A candidate is therefore
> a **leader**, and the things it relates to are its **laggers**. So the method is
> the already-declared `laggers_of(leader, max_hops, as_of)` walking **INBOUND**
> from the candidate to its suppliers — which is what `scripts/capture_showcase.py`
> has been running against real data all along. **`leaders_of` is not needed and
> is not being added**; read `leaders_of` as `laggers_of` and `OUTBOUND` as
> `INBOUND` throughout TASK-3.1 and TASK-3.2, and swap the worked example's
> endpoints (`laggers_of("AAPL", 1)` → `AVGO`, not `leaders_of("GEN", 1)`).
> On the returned `LagEdge`: `leader` is the candidate, `lagger` is the neighbour
> reached — not the reverse.

- TASK-3.2 (impl, `tdd-green`): Implement `laggers_of` as an AQL `INBOUND`
  traversal (edges point supplier→customer; walking inbound from the
  candidate reaches its suppliers, i.e. its laggers) with a `FILTER
  edge.filing_date <= @as_of` and `1..@max_hops` bounds, mapping each result
  path segment to a `LagEdge(leader=<customer>, lagger=<previous hop symbol>,
  relation="supplier", lag_days=<hop index>, correlation=0.0, beta=<pct_revenue
  / 100 if present else 0.0>)`.
  **Before writing the filter clause, probe the installed `python-arango`
  8.3.x's AQL bind-variable handling for a graph traversal directly against
  the running instance** (this project's own D-35/D-44 lesson: verify against
  the installed version, not the docs) rather than assuming syntax.
- TASK-3.3 (refactor, `tdd-refactor`): no behaviour change; extract the AQL
  string to a module constant if it improves readability. Re-run TASK-3.1's
  tests unchanged.

**Verification:** `LAGMATRIX_ARANGO_URL=http://localhost:8529 uv run pytest
tests/test_arango_topology.py -q` → passes (arango running); `uv run pytest
tests/test_arango_topology.py -q` with arango stopped → reports **skipped**,
not failed, not passed-vacuously.

**Halt condition:** if the probe in TASK-3.2 finds `python-arango` 8.3.x
cannot express a bounded, filtered, directed traversal the way assumed, stop
and record what it can do instead — do not fall back to fetching all edges
and filtering hops in Python, which would make PHASE-4's "real graph
traversal" claim false.

## PHASE-4 — Wire the ArangoDB traversal into `graph_retriever`

**Routing:** `tdd-red` → `tdd-green` → `tdd-refactor`. Genuine new behaviour
(new edges appear in `lag_edges`), additive and opt-in.

- TASK-4.1 (test, `tdd-red`): In `tests/test_nodes.py` (or a new file), add a
  fake `ArangoTopology`-shaped object (mirrors the existing `FlakyNewsClient`
  fake-adapter pattern in `test_news_node.py`) exposing `leaders_of(lagger,
  max_hops, as_of)` returning canned `LagEdge`s. Tests:
  - With `runtime.context.arango_topology` set to the fake and
    `max_lag_hops=2`, `retrieve_neighbourhood`'s output `lag_edges_by_key`
    contains the fake's edges **in addition to** the correlation edges, for a
    candidate present in both. Falsifies if either source's edges are
    missing (merge conditional, not additive).
  - The fake is called with `max_hops=runtime.context.max_lag_hops` — assert
    on the fake's captured call args. Falsifies if a hardcoded hop count is
    used instead of reading context (the whole point: `max_lag_hops` has
    never been read by anything).
  - With `arango_topology=None` (the default), output is **byte-identical**
    to today's correlation-only `lag_edges_by_key` for the same inputs.
    Falsifies if adding the new code path changes any existing edge, weight,
    or ordering — this is the test that protects `check_baseline.py`.
- TASK-4.2 (impl, `tdd-green`): In `graph_retriever.py`, after the existing
  correlation loop for candidate `c`, if `runtime.context.arango_topology is
  not None`, call `.leaders_of(c.symbol, runtime.context.max_lag_hops,
  c.as_of)` and extend `c_edges` with the result before writing
  `lag_edges_by_key[key]`.
- TASK-4.3 (refactor, `tdd-refactor`): no behaviour change.

**Verification:** `uv run pytest -q` (44 + new, all pass); **regression
guard:** `uv run python scripts/check_baseline.py --synthetic` and (if real
data present) `uv run python scripts/check_baseline.py` both report
unchanged — proving TASK-4.1's "byte-identical when `None`" claim holds
end-to-end, not just in the unit test.

## PHASE-5 — Embed and index the news subset

**Routing:** one-shot script under `scripts/` — exempt per CLAUDE.md.
**Completion criterion:** the script itself asserts and prints the row count
it wrote; a follow-up AQL count query against the collection matches the
printed number exactly (falsifiable, not "looked reasonable").

- TASK-5.1: Add `scripts/embed_news.py`. Query Postgres (SSH + kubectl + psql,
  same pattern as PHASE-2) for `coalesce(summary, headline)` text,
  `article_id`, `symbol` (via `news_symbol`, one row per tag), `created_at`,
  restricted to breadth<=8, `created_at >= 2025-01-01`, symbol in the 24-ticker
  alert universe (TASK-0.3's subset — **measured 47,763 articles** at plan-
  writing time; re-measure at execution time since the corpus grows daily).
- TASK-5.2: Embed with `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim),
  batched (batch size is a tuning knob, not load-bearing — pick one that keeps
  memory bounded, e.g. 256). **Record the actual wall-clock time** in the
  script's own output; this plan does not pre-commit to a number because CPU
  throughput was not benchmarked in this environment, only estimated.
- TASK-5.3: Upsert into ArangoDB via `langchain-arangodb`'s `ArangoVector`
  (per D-13), one document per `(article_id, symbol)` pair — a single article
  tagging 3 symbols becomes 3 documents, each independently filterable by its
  own `symbol` field, matching how `vector_retriever` will query (per
  candidate symbol).

**Halt condition:** if embedding 47,763 short texts takes materially longer
than a coffee break on the dev machine's 24 cores (say, > 30 minutes), stop
and report the measured throughput rather than letting a one-shot script run
unbounded — the subset can be narrowed further (e.g. `created_at >=
2026-01-01`, ~20,391 articles, matching the real fires' actual date range)
without any design change, but that is a real tradeoff to surface, not to
make silently.

## PHASE-6 — `NewsIndex`: real semantic search

**Routing:** `tdd-red` → `tdd-green` → `tdd-refactor`.

- TASK-6.1 (test, `tdd-red`): `tests/test_vector_index.py`, same
  skip-if-unreachable fixture pattern as PHASE-3, seeded with a handful of
  fixture articles (e.g. one genuinely about a chip supply shortage, one about
  an unrelated topic, both tagged with the same symbol so the metadata filter
  alone cannot distinguish them). Tests, each stating its falsifier:
  - `search(query="chip supply shortage", symbols=["X"], limit=5)` ranks the
    semantically related fixture article above the unrelated one. Falsifies
    if ranking is arbitrary/insertion-order (proves embeddings are doing the
    work, not just a metadata filter).
  - `search(query=..., symbols=["Y"], limit=5)` (a symbol with no seeded
    articles) returns `[]`, not an error and not another symbol's articles —
    proves the metadata filter is real, not advisory.
  - **Probe, not assume, the pre-filter-vs-post-filter question (Q-09)**
    against the installed `langchain-arangodb` 2.1.0 directly: does
    `similarity_search_with_score` accept a metadata filter argument at all,
    and does it filter before or after the ANN search? Record the answer as
    the deciding fact for TASK-6.2's implementation, not as an assumption.
- TASK-6.2 (impl, `tdd-green`): Implement `NewsIndex.search`/`upsert` over
  `ArangoVector`, using whatever filtering mechanism TASK-6.1's probe found.
  If only post-filtering is available, fetch a widened `k` and filter by
  symbol in Python — D-15 already de-escalated this from a latency concern to
  a correctness one, so this is an acceptable, previously-accepted fallback,
  not a new compromise.
- TASK-6.3 (refactor, `tdd-refactor`): no behaviour change.

**Verification:** same skip/run pattern as PHASE-3.

## PHASE-7 — Wire `NewsIndex` into `vector_retriever`, replacing the Alpaca call

**Routing:** `tdd-red` → `tdd-green` → `tdd-refactor`. Genuine behaviour
change to an already-tested node — the three existing tests in
`test_news_node.py` (retry, timeout, halt-and-resume) must keep proving what
they prove, against the new dependency.

- TASK-7.1 (test, `tdd-red`): Rewrite `FlakyNewsClient` /
  `PermanentlyFailingNewsClient` in `test_news_node.py` to the `NewsIndex`
  shape (`.search(query, symbols, limit)`) raising whatever exception type
  PHASE-6 settled on (confirm it is not one of `RetryPolicy`'s excluded types
  — `RuntimeError`/`ValueError`/`OSError`/`TypeError`/`LookupError` — the same
  check TASK-... already had to make for `APIError`). Update the docstrings'
  stated falsifiers accordingly; do not weaken what they prove. Add one new
  test: `retrieve_news` calls `.search()` with a query string derived from the
  candidate (e.g. `f"news relevant to a {c.direction} move in {c.symbol}"`) —
  falsifies if the candidate's own symbol/direction is not in the call.
- TASK-7.2 (impl, `tdd-green`): In `vector_retriever.py`, replace the direct
  `alpaca.data.historical.news.NewsClient` construction and call with
  `runtime.context.vector_index.search(...)`, mapping results to `NewsChunk`
  with `score` now the real similarity score (was `len(n.symbols)`, breadth —
  a real similarity score is strictly more meaningful for the same field, not
  a new field). **Incidental fix, called out explicitly, not silent:** today's
  fallback (`client = NewsClient(os.environ["ALPACA_API_KEY"], ...)`) reads
  `os.environ` directly, violating D-08; the new default construction goes
  through `Settings` like everything else, because the whole point of this
  task is replacing that exact line.
- TASK-7.3 (refactor, `tdd-refactor`): no behaviour change.

**Verification:** `uv run pytest -q` — the three existing tests (now
retargeted) plus the new one, all passing; `grep -rn "alpaca" src/lagmatrix/
graph/nodes/vector_retriever.py` returns nothing.

**Explicitly not changed:** `context_fusion.py`'s treatment of news as
`weight=0.0` context (not counted evidence) — this plan replaces *where the
news comes from*, not *what it's used for*. Changing the latter would reopen
Q-12/D-19, which is out of scope.

## PHASE-8 — CI: ArangoDB as a service container on both matrix legs

**Routing:** infra/config wiring, no behaviour change to `src/` — exempt.
**Completion criterion:** a real CI run (not `act`, not a local approximation)
on both `ubuntu-latest` and `ubuntu-24.04-arm` shows PHASE-3's and PHASE-6's
tests **passing**, not skipped — proving TASK-0.5's manifest check plus this
wiring actually produces a reachable instance in the hosted runner, not just
that the image theoretically supports the architecture.

- TASK-8.1: Add a `services: arangodb: image: arangodb/arangodb:3.12.4.3`
  block to both matrix legs of `.github/workflows/ci.yml`, with
  `ARANGO_NO_AUTH=1` (dev-only credential posture, fine for an ephemeral CI
  container) and a health check on `/_api/version`.
- TASK-8.2: Add a CI step that seeds the same small fixture graph/vector data
  PHASE-3/PHASE-6's tests need (or let the tests do it themselves against the
  already-running service — prefer this, it is one less place for the fixture
  to drift from what the tests assert).

**Halt condition:** if the `aarch64` runner's ArangoDB container fails to
become healthy for a reason unrelated to the architecture (resource limits on
the hosted runner, startup time exceeding a health-check timeout), that is a
CI infra problem to solve on its own terms — do not silently exclude
`aarch64` from PHASE-3/PHASE-6 without recording why.

## PHASE-9 — The live web UI shows both retrieval paths

**Routing:** `scripts/` — exempt (per CLAUDE.md, one-shot/demo scripts). Still
requires an executable, falsifiable check per CLAUDE.md's "unverifiable means
badly designed" — the checks below are that check, not a substitute for one.

- TASK-9.1: In `serve.py`'s `stream()`, build `LagMatrixContext` with
  `arango_topology=ArangoTopology(...)` and `vector_index=NewsIndex(...)` (both
  constructed from `Settings`, pointed at the locally-running instance from
  TASK-0.4) whenever they are reachable; fall back to `None` (today's
  behaviour) with a visible warning in the SSE stream if ArangoDB is not
  running, rather than failing the whole run — this mirrors the existing
  `--allow-real` gating philosophy (degrade the demo, don't crash it) without
  silently hiding that the "real" retrieval is not actually happening.
- TASK-9.2: Change `build_graph(with_news=False)` to
  `build_graph(with_news=True)` in `stream()` — the live UI has never
  exercised `vector_retriever`, real or fake, until this task.
- TASK-9.3: Add two standalone panels to `serve_index.html` plus two `GET`
  endpoints in `Handler`, independent of the candidate pipeline, so the
  showcase is demoable **without** `data/bars.parquet`/`data/fires.csv` (which
  are withheld vendor data, per `README.md`) and without Alpaca credentials —
  the EDGAR-sourced graph and the Postgres-sourced news are this project's own
  data, not Alpaca's:
  - `/graph?symbol=AAPL&hops=2` → calls `ArangoTopology.leaders_of` directly,
    returns the traversal as JSON, rendered as a simple hop-by-hop list.
  - `/search?q=...&symbol=AAPL` → calls `NewsIndex.search` directly, returns
    ranked real headlines with their similarity scores.

**Verification (primary, credential-free, works from a clone):** with
ArangoDB running locally (TASK-0.4) and PHASE-2/PHASE-5's data loaded, `curl
"http://localhost:8000/graph?symbol=GEN&hops=2"` returns JSON containing both
`AVGO` and `AAPL`; `curl "http://localhost:8000/search?q=chip%20supply&symbol=AAPL"`
returns at least one real headline. **Verification (secondary, owner-only,
needs local vendor data):** `scripts/serve.py --allow-real`, then
`/run?source=real&limit=5` includes a `graph_retriever` event for AAPL or
AVGO whose neighbour count reflects real supply-chain edges, and a
`vector_retriever` event with a nonzero real article count — stated as
owner-only because it needs `data/bars.parquet` + `data/fires.csv`, which this
plan does not and cannot provide.

## PHASE-10 — Docs

**Routing:** documentation — exempt.
**Completion criterion:** `git diff --stat` for this phase touches only
`README.md`, `.env.example` (already done in PHASE-1), and
`docs/spikes/overall.md` outcome fields.

- TASK-10.1: Update `README.md`'s script table (`seed_arango.py`'s description
  is currently "bootstrap for the graph store that is not yet built" —
  no longer true; add `embed_news.py`).
- TASK-10.2: Fill in the `Outcome` field on every decision this plan logged in
  PHASE-0 and any it superseded, per this project's own convention that
  `pending` must eventually be replaced with what actually happened —
  including if a phase's halt condition fired and the plan was only partially
  executed.

---

## Halt conditions (project-wide, in addition to each phase's own)

- Any phase whose completion criterion cannot be executed, only asserted:
  stop, diagnose the missing seam per D-38, do not report it as an accepted
  limitation and do not substitute a weaker check.
- If TASK-0.2's deployment-topology decision is overridden by the owner
  (homelab instead of local), PHASE-2 through PHASE-9's "local dev machine"
  framing needs rewriting before those phases execute — this plan does not
  attempt to serve both topologies at once.
- If any existing test in the 44-test baseline is modified in a way that
  changes what it falsifies (not just its fake's shape), that is a defect in
  this plan's execution, not an acceptable side effect — PHASE-7 is the one
  phase where existing tests are touched, and only their fake's interface,
  never their assertions' meaning.
