"""Run the LagMatrix graph over historical candidates using the cached bars.

Uses data/bars.parquet so a demo run needs no Alpaca round-trip for prices.

Usage:  uv run python scripts/run_pipeline.py [--limit N] [--news] [--thread-id ID] [--review]
"""

from __future__ import annotations

import argparse

import pandas as pd

from lagmatrix.pipeline.runner import run_sync


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--news", action="store_true", help="also fetch Alpaca news")
    ap.add_argument("--thread-id", default=None, help="resume/checkpoint under this thread id")
    ap.add_argument(
        "--review", action="store_true",
        help="halt on a contradicted verdict for human confirmation (D-19)",
    )
    args = ap.parse_args()

    bars = pd.read_parquet("data/bars.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")

    out, thread_id, interrupt = run_sync(
        closes=closes, with_news=args.news, limit=args.limit, thread_id=args.thread_id,
        halt_on_contradicted=args.review,
    )
    print(f"thread_id: {thread_id}")

    if interrupt is not None:
        print(f"\ninterrupted: {interrupt['reason']}")
        for c in interrupt["contradicted"]:
            print(f"  {c['symbol']} @ {c['as_of']}: {c['rationale']}")
        print(f"\nresume with: --thread-id {thread_id}")
        return

    print(f"\n{len(out)} assessments\n")
    for a in out:
        print(f"{a.candidate.symbol:6} {a.candidate.direction:4} {a.candidate.as_of}  "
              f"{a.verdict:14} eff={a.effective_evidence:5.2f}  "
              f"+{len(a.supporting)}/-{len(a.contradicting)}")
    if out:
        a = out[0]
        print(f"\n--- {a.candidate.symbol} rationale ---\n{a.rationale}")
        for e in (a.supporting + a.contradicting)[:4]:
            mark = "+" if e.supports else "-"
            print(f"  {mark} w={e.weight:.3f}  {e.detail}")


if __name__ == "__main__":
    main()
