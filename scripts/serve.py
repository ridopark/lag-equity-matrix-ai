"""A small local web UI that streams the graph executing, live.

Not a replay. Each browser request starts a real `graph.astream(...)` and pushes
every node update over Server-Sent Events as it happens, so what you watch is
the run, with its own wall-clock timings.

Stdlib only — `ThreadingHTTPServer` plus SSE. A web framework would be two more
dependencies for one page.

    uv run python scripts/serve.py                # synthetic universe, port 8000
    uv run python scripts/serve.py --port 8080
    uv run python scripts/serve.py --allow-real   # also offer the real data

The synthetic universe is committed, so this works from a clone with no
credentials. The real inputs (`data/bars.parquet`, `data/fires.csv`) are not,
and the toggle for them stays off unless you pass --allow-real.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import pathlib
import socket
import sys
import tempfile
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import capture_showcase as cap  # noqa: E402
from capture_trace import detail  # noqa: E402

from lagmatrix.adapters.candidates import ExternalSignals, MarketScan  # noqa: E402
from lagmatrix.graph.builder import build_graph  # noqa: E402
from lagmatrix.graph.context import LagMatrixContext  # noqa: E402

SYNTHETIC_CLOSES = "tests/fixtures/synthetic-closes.parquet"
SYNTHETIC_FALLBACK_DATE = "2026-05-11"  # only when no price file can be read


_COMOVE_CACHE: dict[str, object] = {}


def COMOVE_CLOSES():
    """Closes for co-movement: the long file, read once per process.

    Falls back to the wide file so a machine without the 10-year extract still
    starts — it will simply find no edges, which `comovement_edges` reports as
    an empty list rather than an error.
    """
    if "closes" not in _COMOVE_CACHE:
        for path in ("data/bars-10y.parquet", "data/bars.parquet", SYNTHETIC_CLOSES):
            try:
                bars = pd.read_parquet(path)
            except Exception:
                continue
            _COMOVE_CACHE["closes"] = (
                bars.pivot_table(index="timestamp", columns="symbol", values="close")
                if "timestamp" in bars.columns else bars)
            break
    return _COMOVE_CACHE.get("closes")


def default_as_of() -> str:
    """The last session actually present in the data, not a baked-in constant.

    The page and every endpoint used to default to a hardcoded 2026-05-11 while
    `data/bars.parquet` ran months past it, so the live demo silently assessed a
    stale date. Reads the real file when it is available and falls back to the
    synthetic fixture, then to the old constant, so a machine with neither still
    starts.
    """
    for path in ("data/bars.parquet", SYNTHETIC_CLOSES):
        try:
            bars = pd.read_parquet(path)
        except Exception:
            continue
        idx = (bars.pivot_table(index="timestamp", columns="symbol", values="close").index
               if "timestamp" in bars.columns else bars.index)
        if len(idx):
            return str(idx[-1].date())
    return SYNTHETIC_FALLBACK_DATE
SYNTHETIC_FIRES = "tests/fixtures/synthetic-fires.csv"
PAGE = pathlib.Path(__file__).parent / "serve_index.html"

ALLOW_REAL = False
ARANGO_URL = os.environ.get("LAGMATRIX_ARANGO_URL", "http://localhost:19999")
FANNED = {"graph_retriever", "leader_state", "vector_retriever"}


def arango_db():
    """The graph/vector handle, or None when the homelab is not tunnelled.

    Returning None rather than raising is deliberate: the correlation half of
    this pipeline needs no database, so the page should still run and say the
    GraphRAG half is unavailable, instead of failing whole.
    """
    parsed = urlparse(ARANGO_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 8529), timeout=1):
            pass
    except OSError:
        return None
    try:
        from arango import ArangoClient
        pw = pathlib.Path(os.path.expanduser("~/.lagmatrix-arango-pw")).read_text().strip()
        return ArangoClient(hosts=ARANGO_URL).db("lagmatrix", username="root", password=pw)
    except Exception:
        return None


def load(source: str, db=None, as_of: str | None = None
         ) -> tuple[pd.DataFrame, list, frozenset[str]]:
    """Closes, candidates, and symbols to keep out of every neighbourhood.

    The third element is empty for the two alert-fed sources. It exists for
    `scan`, where the companies that *originated* the candidates must not also
    be allowed to score them — see the note in `stream()`.
    """
    if source == "scan":
        if not ALLOW_REAL:
            raise PermissionError("scan needs real market data; start with --allow-real")
        if db is None:
            raise RuntimeError("scan needs ArangoDB; none reachable at " + ARANGO_URL)
        from lagmatrix.adapters.arango import ArangoTopology

        bars = pd.read_parquet("data/bars.parquet")
        closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
        excluded = frozenset(
            ln.split(",")[0] for ln in
            pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln
        )
        scan_date = date.fromisoformat(as_of or default_as_of())
        scan = MarketScan(closes, ArangoTopology(db), excluded_symbols=excluded)
        return closes, scan.candidates(scan_date), frozenset(scan.shocked_leaders(scan_date))
    if source == "real":
        if not ALLOW_REAL:
            raise PermissionError("real data not enabled; start with --allow-real")
        bars = pd.read_parquet("data/bars.parquet")
        closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
        return closes, ExternalSignals().candidates(), frozenset()
    closes = pd.read_parquet(SYNTHETIC_CLOSES)
    return closes, ExternalSignals(SYNTHETIC_FIRES).candidates(), frozenset()


async def stream(source: str, limit: int | None, emit, *,
                 as_of: str | None = None) -> None:
    import aiosqlite
    from langgraph.cache.memory import InMemoryCache
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command

    from lagmatrix.adapters.arango import ArangoTopology
    from lagmatrix.adapters.vector import NewsIndex
    from lagmatrix.pipeline.runner import _ALLOWED_MODELS

    db = arango_db()
    closes, all_c, origin_leaders = load(source, db, as_of)
    cands = all_c[:limit] if limit else all_c
    ctx = LagMatrixContext(
        closes=closes,
        # The scan's originating movers are excluded alongside the candidates
        # themselves. Without this a shocked leader Y, which is often correlated
        # with the supplier X it produced, re-enters X's neighbourhood and its
        # move is counted as evidence for the candidate it created (REQ-7).
        signal_universe={c.symbol for c in all_c} | origin_leaders,
        arango_topology=ArangoTopology(db) if db is not None else None,
        vector_index=NewsIndex(db) if db is not None else None,
        max_lag_hops=2,
        news_limit=6,
        halt_on_contradicted=True,
    )

    emit("start", {
        "source": source, "candidates": len(cands),
        "symbols": int(closes.shape[1]), "sessions": int(closes.shape[0]),
        "graphrag": db is not None,
        # In scan mode two stages already ran before the graph was entered:
        # the market sweep and the traversal that turned movers into candidates.
        # The page draws them, otherwise the inversion is invisible and the
        # diagram still reads as though neighbours were looked up first.
        "scan": ({"swept": int(closes.shape[1]),
                  "movers": len(origin_leaders),
                  "found": len(all_c)} if source == "scan" else None),
    })

    t0 = time.perf_counter()
    tasks: dict = {}

    def absorb(mode, data):
        now = round((time.perf_counter() - t0) * 1000, 1)
        if mode == "updates":
            for node, upd in data.items():
                key, txt = detail(node, upd)
                emit("event", {"t_ms": now, "node": node, "key": key, "detail": txt or ""})
        elif mode == "debug":
            kind, pay = data.get("type"), data.get("payload", {})
            if kind == "task":
                tasks[pay.get("id")] = {
                    "node": pay.get("name"), "step": data.get("step"), "start_ms": now,
                    "key": getattr((pay.get("input") or {}).get("candidate"), "symbol", None)
                    if pay.get("name") in FANNED else None,
                }
            elif kind == "task_result" and pay.get("id") in tasks:
                s = tasks[pay["id"]]
                s["end_ms"] = now
                emit("span", s)
            elif kind == "checkpoint":
                emit("checkpoint", {"t_ms": now, "step": data.get("step"),
                                    "id": (pay.get("config") or {}).get(
                                        "configurable", {}).get("checkpoint_id")})

    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODELS)
    with tempfile.TemporaryDirectory() as tmp:
        async with aiosqlite.connect(f"{tmp}/live.sqlite") as conn:
            graph = build_graph(with_news=db is not None,
                                checkpointer=AsyncSqliteSaver(conn, serde=serde),
                                cache=InMemoryCache())
            config = {"configurable": {"thread_id": f"live-{int(time.time())}"}}
            # publisher prints per assessment; that would interleave with the SSE body
            with contextlib.redirect_stdout(io.StringIO()):
                async for mode, data in graph.astream(
                    {"candidates": cands}, context=ctx, config=config,
                    stream_mode=["updates", "debug"],
                ):
                    absorb(mode, data)
                snap = await graph.aget_state(config)
                if snap.interrupts:
                    payload = snap.interrupts[0].value
                    emit("interrupt", {
                        "t_ms": round((time.perf_counter() - t0) * 1000, 1),
                        "payload": payload,
                    })
                    async for mode, data in graph.astream(
                        Command(resume=True), context=ctx, config=config,
                        stream_mode=["updates", "debug"],
                    ):
                        absorb(mode, data)
                    snap = await graph.aget_state(config)

    emit("done", {
        "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        "thread_id": config["configurable"]["thread_id"],
        "assessments": [
            {
                "symbol": a.candidate.symbol,
                "as_of": a.candidate.as_of.isoformat(),
                "direction": a.candidate.direction,
                "verdict": a.verdict,
                "effective_evidence": round(a.effective_evidence, 3),
                "n_supporting": len(a.supporting),
                "n_contradicting": len(a.contradicting),
                "rationale": a.rationale,
                "description": a.description,
            }
            for a in snap.values.get("assessments", [])
        ],
    })


def movers(as_of: str, top_n: int = 25) -> dict:
    """Step 1 of the UI: what moved on this date, and which of those the graph covers.

    Not a pipeline run — no LangGraph, no candidates, no verdicts. Just
    `MarketScan.shocked_leaders`, which the scan already computes, plus a
    follower count per mover so a reader can see what the graph does and does
    not reach.

    Coverage is the honest headline here and is always reported, not only when
    it is bad: measured across 8 dates, 973 movers had 34 with any disclosed
    supplier (3.5%), and two of those dates had none at all. Movers sort
    covered-first then by |z|, so every covered name on a date is visible; the
    rest fill the remaining slots as context rather than being hidden.
    """
    if not ALLOW_REAL:
        raise PermissionError("movers needs real market data; start with --allow-real")
    db = arango_db()
    bars = pd.read_parquet("data/bars.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    excluded = frozenset(
        ln.split(",")[0] for ln in
        pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln
    )
    d = date.fromisoformat(as_of or default_as_of())
    topo = None
    if db is not None:
        from lagmatrix.adapters.arango import ArangoTopology
        topo = ArangoTopology(db)
    scan = MarketScan(closes, topo, excluded_symbols=excluded)
    shocked = scan.shocked_leaders(d)
    counts = {}
    if topo is not None:
        for sym in shocked:
            try:
                counts[sym] = len(topo.laggers_of(sym, 1, d))
            except Exception:
                counts[sym] = 0
    rows = [{"symbol": s, "z": round(z, 3), "followers": counts.get(s, 0)}
            for s, z in shocked.items()]
    rows.sort(key=lambda r: (-(r["followers"] > 0), -abs(r["z"]), r["symbol"]))
    return {
        "as_of": str(d),
        "swept": int(closes.shape[1]),
        "movers_found": len(rows),
        "customers_covered": sum(1 for r in rows if r["followers"] > 0),
        "movers": rows[:top_n],
    }


def followers(symbol: str, as_of: str, trail: int = 250,
              min_abs_corr: float = 0.5, top_n: int = 25) -> dict:
    """Step 2 of the UI: given a leader, the names that move with it.

    Edges come from `lagmatrix.comovement` — measured pairwise correlation of
    market-excess returns over the `trail` sessions ending strictly before
    `as_of` — each with a Fisher confidence interval.

    The interval says how precisely the co-movement is measured. It is **not** a
    probability about what happens next: D-93 nulled lagged prediction across
    2.47M pairs and D-94 showed chains carry sign without magnitude, while
    contemporaneous co-movement replicates at 0.640 (D-95). Same-day, not
    next-day.

    The leader's own forward distribution is included (D-96) because a reader
    asked for it, and it is honestly a coin flip: after a >= 2 sigma move, a
    symbol has historically gone nowhere in particular.
    """
    if not ALLOW_REAL:
        raise PermissionError("followers needs real market data; start with --allow-real")
    from lagmatrix.comovement import comovement_edges

    # Co-movement needs history, and the two price files serve different jobs:
    # `bars.parquet` sweeps wider (3,204 symbols) but reaches back only 159
    # sessions, which is fewer than `trail`, so it yields no edges at all.
    # `bars-10y.parquet` has 2,514 sessions, is what D-95's 0.640 replication
    # was measured on, and contained every one of the 43 movers on 2026-09-04.
    closes = COMOVE_CLOSES()
    excluded = frozenset(
        ln.split(",")[0] for ln in
        pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln
    )
    d = date.fromisoformat(as_of or default_as_of())
    sym = symbol.upper()
    if sym not in closes.columns:
        return {"error": f"{sym} not in the price file"}

    edges = comovement_edges(closes, d, trail=trail, min_abs_corr=min_abs_corr,
                             exclude=excluded)
    mine = [e for e in edges if sym in (e.a, e.b)]
    rows = [{
        "symbol": e.b if e.a == sym else e.a,
        "corr": round(e.corr, 4),
        "ci_low": round(e.ci_low, 4),
        "ci_high": round(e.ci_high, 4),
        "n_sessions": e.n_sessions,
        "flag": e.flag,
    } for e in mine]
    rows.sort(key=lambda r: -abs(r["corr"]))

    scan = MarketScan(closes, None, excluded_symbols=excluded)
    z = scan.shocked_leaders(d).get(sym)
    return {
        "leader": sym,
        "as_of": str(d),
        "leader_z": round(z, 3) if z is not None else None,
        "trail": trail,
        "min_abs_corr": min_abs_corr,
        "followers_found": len(rows),
        "followers": rows[:top_n],
        # D-96, measured on 40,545 held-out episodes across 2,153 symbols
        "own_move": {
            "note": "after a >= 2 sigma move this symbol has historically gone "
                    "nowhere in particular",
            "continued_pct": 48.7,
            "sd_sigma": 1.43,
        },
    }


def neighbourhood(symbol: str, as_of: str) -> dict:
    """Supply edges with their filing sentences, plus real two-hop co-mention.

    Served on demand so clicking a node in the page is a live query, not a
    lookup into something baked in at build time.
    """
    db = arango_db()
    if db is None:
        return {"error": "ArangoDB not reachable"}
    rows = list(db.aql.execute(cap.TRAVERSAL_AQL,
                               bind_vars={"start": f"equity/{symbol}", "hops": 2,
                                          "as_of": as_of}))
    for r in rows:
        sent, pct = cap.disclosure(r.get("says") or "", r.get("named"))
        quotable = bool(sent) and sent[:1].isupper()
        r["verified"] = quotable
        r["sentence"] = sent if quotable else None
        r["pct"] = pct if quotable else None
        r.pop("says", None)
    excluded = [ln.split(",")[0] for ln in
                pathlib.Path("data/excluded-etfs.csv").read_text().splitlines()[1:] if ln]
    two = list(db.aql.execute(cap.COMENTION_AQL,
                              bind_vars={"start": f"equity/{symbol}", "as_of": as_of,
                                         "minpmi": 2.0, "excluded": excluded}))
    return {"symbol": symbol, "as_of": as_of, "edges": rows, "twohop": two}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter than the default
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/config":
            self._json({"allow_real": ALLOW_REAL, "graphrag": arango_db() is not None,
                        "arango_url": ARANGO_URL, "default_as_of": default_as_of()})
            return
        if url.path == "/movers":
            q = parse_qs(url.query)
            try:
                self._json(movers((q.get("as_of") or [default_as_of()])[0],
                                  int((q.get("top_n") or ["25"])[0])))
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"})
            return
        if url.path == "/followers":
            q = parse_qs(url.query)
            s = (q.get("symbol") or [""])[0].upper()
            if not s.isalnum():
                self.send_error(400, "symbol must be alphanumeric")
                return
            try:
                self._json(followers(s, (q.get("as_of") or [default_as_of()])[0],
                                     top_n=int((q.get("top_n") or ["25"])[0])))
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"})
            return
        if url.path == "/graph":
            q = parse_qs(url.query)
            sym = (q.get("symbol") or [""])[0].upper()
            as_of = (q.get("as_of") or [default_as_of()])[0]
            if not sym.isalnum():
                self.send_error(400, "symbol must be alphanumeric")
                return
            try:
                self._json(neighbourhood(sym, as_of))
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"})
            return
        if url.path == "/run":
            self._run(parse_qs(url.query))
            return
        self.send_error(404)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self, q):
        source = (q.get("source") or ["synthetic"])[0]
        raw = (q.get("limit") or [""])[0]
        limit = int(raw) if raw.isdigit() and int(raw) > 0 else None
        as_of_str = (q.get("as_of") or [default_as_of()])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(kind, payload):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()

        try:
            asyncio.run(stream(source, limit, emit, as_of=as_of_str))
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser navigated away mid-run
        except Exception as e:  # surface the failure in the UI rather than a dead stream
            with contextlib.suppress(OSError):
                emit("error", {"message": f"{type(e).__name__}: {e}"})
        self.close_connection = True


def main() -> None:
    global ALLOW_REAL
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--allow-real", action="store_true",
                    help="offer data/bars.parquet + data/fires.csv as a source")
    args = ap.parse_args()
    ALLOW_REAL = args.allow_real

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"  LagMatrix live view  http://{args.host}:{args.port}")
    print(f"  sources: synthetic{' + real' if ALLOW_REAL else ''}   (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    main()
