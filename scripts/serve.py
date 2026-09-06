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
import pathlib
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from capture_trace import detail  # noqa: E402

from lagmatrix.adapters.candidates import ExternalSignals  # noqa: E402
from lagmatrix.graph.builder import build_graph  # noqa: E402
from lagmatrix.graph.context import LagMatrixContext  # noqa: E402

SYNTHETIC_CLOSES = "tests/fixtures/synthetic-closes.parquet"
SYNTHETIC_FIRES = "tests/fixtures/synthetic-fires.csv"
PAGE = pathlib.Path(__file__).parent / "serve_index.html"

ALLOW_REAL = False


def load(source: str) -> tuple[pd.DataFrame, list]:
    if source == "real":
        if not ALLOW_REAL:
            raise PermissionError("real data not enabled; start with --allow-real")
        bars = pd.read_parquet("data/bars.parquet")
        closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
        return closes, ExternalSignals().candidates()
    closes = pd.read_parquet(SYNTHETIC_CLOSES)
    return closes, ExternalSignals(SYNTHETIC_FIRES).candidates()


async def stream(source: str, limit: int | None, emit) -> None:
    closes, all_c = load(source)
    cands = all_c[:limit] if limit else all_c
    ctx = LagMatrixContext(closes=closes, signal_universe={c.symbol for c in all_c})

    emit("start", {
        "source": source, "candidates": len(cands),
        "symbols": int(closes.shape[1]), "sessions": int(closes.shape[0]),
    })

    graph = build_graph(with_news=False)
    t0 = time.perf_counter()
    # publisher prints per assessment; that would interleave with the SSE body
    with contextlib.redirect_stdout(io.StringIO()):
        async for chunk in graph.astream(
            {"candidates": cands}, context=ctx, stream_mode="updates"
        ):
            ms = round((time.perf_counter() - t0) * 1000, 1)
            for node, upd in chunk.items():
                key, txt = detail(node, upd)
                emit("event", {"t_ms": ms, "node": node, "key": key, "detail": txt})
        final = await graph.ainvoke({"candidates": cands}, context=ctx)

    emit("done", {
        "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        "assessments": [
            {
                "symbol": a.candidate.symbol,
                "as_of": a.candidate.as_of.isoformat(),
                "direction": a.candidate.direction,
                "verdict": a.verdict,
                "effective_evidence": round(a.effective_evidence, 3),
                "n_supporting": len(a.supporting),
                "n_contradicting": len(a.contradicting),
            }
            for a in final.get("assessments", [])
        ],
    })


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
            self._json({"allow_real": ALLOW_REAL})
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

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(kind, payload):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()

        try:
            asyncio.run(stream(source, limit, emit))
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
