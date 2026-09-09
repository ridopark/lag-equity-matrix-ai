"""Nightly ingest: new bars and new news into the stores, then rebuild the edges.

Two independent chains, run concurrently and deliberately never joined:

    START ─┬─> bars ──> comovement ──> END
           └─> news ──> vectors ─────> END

No join node, on purpose. D-89 established that a join whose in-edges complete
in different supersteps fires twice, and `errors` here is a concatenating
channel, so a join would double its contents. The two chains have nothing to
say to each other, so there is nothing to join.

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
from langgraph.types import RetryPolicy, Send

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


def last_article_date() -> str | None:
    """Latest article already embedded, so news resumes from there."""
    try:
        import serve
        db = serve.arango_db()
        if db is None:
            return None
        rows = list(db.aql.execute(
            "FOR a IN article COLLECT AGGREGATE hi = MAX(a.date) RETURN hi"))
        return rows[0] if rows and rows[0] else None
    except Exception:
        return None


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


def node_bars(state: IngestState) -> dict:
    last = last_bar_date()
    today = datetime.now(UTC).date()
    if last is not None and last >= today - timedelta(days=1):
        return {"done": [f"bars: already current through {last}"]}
    out = _run([sys.executable, "scripts/fetch_bars.py"], state.get("dry_run", False))
    return {"done": [f"bars: fetched through {last_bar_date()} {out}"]}


def node_news(state: IngestState) -> dict:
    since = last_article_date()
    cmd = [sys.executable, "scripts/load_news.py", "--top-liquid", "500"]
    if since:
        cmd += ["--start", since]
    out = _run(cmd, state.get("dry_run", False))
    return {"done": [f"news: from {since or 'the beginning'} {out}"]}


def node_vectors(state: IngestState) -> dict:
    since = last_article_date() or "2025-01-01"
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
    from lagmatrix.comovement import comovement_edges

    db = serve.arango_db()
    if db is None:
        return {"errors": ["comovement: ArangoDB not reachable; edges not written"]}
    closes = serve.COMOVE_CLOSES()
    excluded = frozenset(
        ln.split(",")[0] for ln in
        pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln)
    d = date.fromisoformat(state["as_of"])
    edges = comovement_edges(closes, d, trail=250, min_abs_corr=0.5, exclude=excluded)
    upsert_comovement(db, edges, d)
    return {"done": [f"comovement: {len(edges):,} edges upserted as of {d}"]}


def build():
    g = StateGraph(IngestState)
    retry = RetryPolicy(max_attempts=3)
    g.add_node("bars", node_bars, retry_policy=retry)
    g.add_node("news", node_news, retry_policy=retry)
    g.add_node("vectors", node_vectors, retry_policy=retry)
    g.add_node("comovement", node_comovement, retry_policy=retry)
    g.add_conditional_edges(START, lambda s: [Send("bars", s), Send("news", s)],
                            ["bars", "news"])
    g.add_edge("bars", "comovement")
    g.add_edge("news", "vectors")
    g.add_edge("comovement", END)
    g.add_edge("vectors", END)
    return g.compile()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None,
                    help="date to compute co-movement edges for (default: latest session)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would run without fetching or writing")
    args = ap.parse_args()

    import serve
    as_of = args.as_of or serve.default_as_of()
    print(f"daily ingest, as of {as_of}{'  [dry run]' if args.dry_run else ''}")
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
