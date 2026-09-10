"""Nightly ingest: new bars and new news into the stores, then rebuild the edges.

One ordered chain. Every edge is a real data dependency:

    START ─> extract_fires ─> bars ─> long_bars ─> comovement ─> news ─> vectors ─> END

This replaced two chains fanned out from START and never joined. **The claim
that motivated that shape — "the two chains have nothing to say to each
other" — was false**, and is withdrawn here rather than quietly edited away:
`node_news` runs `load_news.py --top-liquid`, which reads the
`data/bars.parquet` that `node_bars` writes, and both sat in the same
superstep. Which version it read was settled by subprocess startup timing, and
because `fetch_bars.py` fetches for minutes before it writes, the read always
won — so news ranked liquidity from the previous run's file, every night,
silently.

No join node, still, and D-89's mechanism is untouched: a join whose in-edges
complete in different supersteps fires twice, and `errors` here is a
concatenating channel. The reason there is no join now is that **no node has
two in-edges** — not that anything is independent.

LangGraph earns its place for three specific reasons, not decoration:
  - `RetryPolicy` — Alpaca drops long-lived connections on a decade-scale pull.
  - checkpointing — a nightly run that dies at `vectors` resumes there instead
    of re-fetching and re-embedding everything.
  - the two chains genuinely run at once.

Every step is incremental and idempotent (D-97): re-running a night is a no-op,
not a duplicate. Nothing here drops a collection.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, TypedDict

import pandas as pd
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

BARS = "data/bars.parquet"
LONG_BARS = "data/bars-10y.parquet"


def _add(a: list, b: list) -> list:
    return a + b


class IngestState(TypedDict, total=False):
    as_of: str
    dry_run: bool
    done: Annotated[list[str], _add]
    errors: Annotated[list[str], _add]


def last_bar_date() -> date | None:
    try:
        b = pd.read_parquet(BARS)
    except Exception:
        return None
    return pd.to_datetime(b["timestamp"]).max().date()


def long_bar_date() -> date | None:
    try:
        b = pd.read_parquet(LONG_BARS)
    except Exception:
        return None
    return pd.to_datetime(b["timestamp"]).max().date()


def last_article_date() -> tuple[bool, str | None, str]:
    """Latest article already embedded, so news resumes from there.

    Returns `(ok, since, reason)`: `ok` is `False` for an infrastructure
    failure or data corruption -- distinct from a genuinely empty `article`
    collection, which is `(True, None, "")` and lets a first run proceed.
    """
    import serve
    db = serve.arango_db()
    if db is None:
        return False, None, f"ArangoDB unavailable: {serve.arango_reason()}"
    rows = list(db.aql.execute(
        "FOR a IN article COLLECT AGGREGATE hi = MAX(a.date) RETURN hi"))
    hi = rows[0] if rows else None
    if not hi:
        return True, None, ""
    # This value is passed to `load_vectors.py --since`, which reaches psql
    # inside the *copytrade* namespace. It comes back out of a database, not
    # from an operator, so it is validated here as well as there -- a stored
    # value must never be trusted just because we are the ones who stored it.
    try:
        return True, date.fromisoformat(str(hi)).isoformat(), ""
    except ValueError:
        return False, None, f"stored article date malformed: {hi!r}"


def _run(cmd: list[str], dry: bool) -> str:
    """Shell out to an existing loader. They are CLI tools with their own
    argparse and their own idempotency; re-implementing them here would be the
    duplication CLAUDE.md warns about."""
    if dry:
        return f"DRY {' '.join(cmd)}"
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[2] if len(cmd) > 2 else cmd}: {r.stderr[-400:]}")
    return (r.stdout or "").strip()[-200:]


def node_extract_fires(state: IngestState) -> dict:
    """Refresh `data/fires.csv` from the trading system's audit_log.

    Nothing used to run this, so the file was a static artefact that quietly
    went stale (3 signals behind when measured). It runs first because
    `fetch_bars.py` reads it to set the price window, and `load_vectors.py`
    reads it for the embedding universe.

    Safe to regenerate nightly for a structural reason, not a hopeful one:
    `fetch_daily_bars.py` takes the long file's universe from the file itself
    (`existing["symbol"].unique()`), so this cannot reach D-95's frozen
    2,183-symbol cohort. The alert set selects candidates; the co-movement
    graph is built from price history regardless (D-27).
    """
    out = _run([sys.executable, "scripts/extract_fires.py"],
               state.get("dry_run", False))
    return {"done": [f"fires: refreshed {out}"]}


def node_bars(state: IngestState) -> dict:
    last = last_bar_date()
    today = datetime.now(UTC).date()
    if last is not None and last >= today - timedelta(days=1):
        return {"done": [f"bars: already current through {last}"]}
    out = _run([sys.executable, "scripts/fetch_bars.py"], state.get("dry_run", False))
    return {"done": [f"bars: fetched through {last_bar_date()} {out}"]}


def node_long_bars(state: IngestState) -> dict:
    last = long_bar_date()
    today = datetime.now(UTC).date()
    if last is not None and last >= today - timedelta(days=1):
        return {"done": [f"long_bars: already current through {last}"]}
    out = _run([sys.executable, "scripts/fetch_daily_bars.py"], state.get("dry_run", False))
    return {"done": [f"long_bars: fetched through {long_bar_date()} {out}"]}


def node_news(state: IngestState) -> dict:
    ok, since, reason = last_article_date()
    if not ok:
        return {"errors": [f"news: {reason}"]}
    cmd = [sys.executable, "scripts/load_news.py", "--top-liquid", "500"]
    if since:
        cmd += ["--start", since]
    out = _run(cmd, state.get("dry_run", False))
    return {"done": [f"news: from {since or 'the beginning'} {out}"]}


def node_vectors(state: IngestState) -> dict:
    ok, since, reason = last_article_date()
    if not ok:
        return {"errors": [f"vectors: {reason}"]}
    since = since or "2025-01-01"
    out = _run([sys.executable, "scripts/load_vectors.py", "--since", since],
               state.get("dry_run", False))
    return {"done": [f"vectors: embedded from {since} {out}"]}


def node_comovement(state: IngestState) -> dict:
    """Recompute the co-movement edges for `as_of` and upsert them (D-95).

    Reads the long bars file: co-movement needs `trail` sessions of history and
    `bars.parquet` holds only ~159, which yields no edges at all.
    """
    if state.get("dry_run"):
        return {"done": ["comovement: DRY"]}
    import serve

    from lagmatrix.adapters.arango import upsert_comovement
    from lagmatrix.comovement import comovement_edges, session_available

    db = serve.arango_db()
    if db is None:
        return {"errors": [f"comovement: ArangoDB unavailable, edges not written: "
                           f"{serve.arango_reason()}"]}
    closes = serve.COMOVE_CLOSES()
    d = date.fromisoformat(state.get("as_of") or serve.default_as_of())
    ok, reason = session_available(closes, d, trail=250)
    if not ok:
        return {"errors": [f"comovement: {reason}"]}
    excluded = frozenset(
        ln.split(",")[0] for ln in
        pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln)
    edges = comovement_edges(closes, d, trail=250, min_abs_corr=0.5, exclude=excluded)
    upsert_comovement(db, edges, d)
    return {"done": [f"comovement: {len(edges):,} edges upserted as of {d}"]}


def build():
    """A single ordered chain. Every edge is a real data dependency.

        extract_fires -> bars -> long_bars -> comovement -> news -> vectors

    This replaces two chains fanned out from START and deliberately never
    joined (D-89). That design rested on the claim that the chains had nothing
    to say to each other, and the claim was false: `node_news` runs
    `load_news.py --top-liquid`, which reads the `data/bars.parquet` that
    `node_bars` writes -- in the same superstep. Which version it read was
    decided by subprocess startup timing, and since `fetch_bars.py` takes
    minutes to fetch before it writes, the read essentially always won. So
    `news` ranked liquidity from the *previous* run's file, every night,
    silently. Correct by coincidence is what D-109 was about.

    D-89's mechanism is untouched and still binding: a join whose in-edges
    complete in different supersteps fires twice into a concatenating channel.
    The reason there is no join here is that **no node has two in-edges** --
    not that anything is independent.

    Serial rather than the minimal fix, for a measured reason. Ordering
    `vectors` after `comovement` without a join is only possible in a chain,
    and keeping them concurrent put 1,018 MiB (comovement) and 617 MiB
    (load_vectors) in one superstep at the moment `load_vectors` triggers
    ArangoDB's index rebuild -- against ~2.8 GiB free on a node that also runs
    real-money trading. Serial peak is one node at a time, about 1,018 MiB.
    The cost is wall-clock on a job that has all night.
    """
    g = StateGraph(IngestState)
    retry = RetryPolicy(max_attempts=3)
    g.add_node("extract_fires", node_extract_fires, retry_policy=retry)
    g.add_node("bars", node_bars, retry_policy=retry)
    g.add_node("long_bars", node_long_bars, retry_policy=retry)
    g.add_node("comovement", node_comovement, retry_policy=retry)
    g.add_node("news", node_news, retry_policy=retry)
    g.add_node("vectors", node_vectors, retry_policy=retry)
    g.add_edge(START, "extract_fires")
    g.add_edge("extract_fires", "bars")
    g.add_edge("bars", "long_bars")
    g.add_edge("long_bars", "comovement")
    g.add_edge("comovement", "news")
    g.add_edge("news", "vectors")
    g.add_edge("vectors", END)
    return g.compile()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None,
                    help="date to compute co-movement edges for (default: latest session)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would run without fetching or writing")
    args = ap.parse_args()

    as_of = args.as_of
    if as_of:
        banner = f"daily ingest, as of {as_of}"
    else:
        banner = "daily ingest, as of: resolved after the bars fetch"
    print(f"{banner}{'  [dry run]' if args.dry_run else ''}")
    out = asyncio.run(build().ainvoke({"as_of": as_of, "dry_run": args.dry_run,
                                       "done": [], "errors": []}))
    for line in out.get("done", []):
        print(f"  ok   {line}")
    for line in out.get("errors", []):
        print(f"  FAIL {line}")
    if out.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    os.chdir(pathlib.Path(__file__).resolve().parent.parent)
    main()
