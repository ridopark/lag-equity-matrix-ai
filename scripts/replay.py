"""Print the checkpoint history for a run (PLAN-2026-09-03, TASK-3.7).

D-35's "time travel, cheaply": a script reading `get_state_history`, not a
graph feature. For each checkpoint, prints the step, the nodes that ran to
reach it, and the assessments present in its state.

Usage:  uv run python scripts/replay.py <thread_id>
"""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from lagmatrix.domain.models import Assessment, Bar, Candidate, Evidence, LagEdge, NewsChunk, Shock
from lagmatrix.graph.builder import build_graph

CHECKPOINT_DB_PATH = "data/checkpoints.sqlite"

# D-44: same allowlist as runner.py, needed to read the checkpoints back as
# domain models rather than dicts (and without the deprecation warning).
_ALLOWED_MODELS = [
    ("lagmatrix.domain.models", cls.__name__)
    for cls in (Bar, Shock, LagEdge, NewsChunk, Candidate, Evidence, Assessment)
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("thread_id")
    args = ap.parse_args()

    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MODELS)
    config = {"configurable": {"thread_id": args.thread_id}}

    with closing(sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)) as conn:
        saver = SqliteSaver(conn, serde=serde)
        graph = build_graph(checkpointer=saver)

        for snap in graph.get_state_history(config):
            step = snap.metadata.get("step")
            nodes = [t.name for t in snap.tasks]
            assessments = snap.values.get("assessments") or []
            symbols = [a.candidate.symbol for a in assessments]
            print(f"step {step:>3}  nodes={nodes}  assessments={symbols}")


if __name__ == "__main__":
    main()
