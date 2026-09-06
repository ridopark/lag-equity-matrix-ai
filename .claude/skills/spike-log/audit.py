#!/usr/bin/env python3
"""Integrity check for the decision log.

Catches the ways a decision log rots silently: references to entries that no
longer exist, Tentative decisions whose gating question has since been closed,
duplicate IDs, and a Spike Index that has drifted from the files on disk.

Usage:  python3 .claude/skills/spike-log/audit.py [path/to/overall.md]
Exit:   0 clean, 1 problems found, 2 log not found.
"""

import pathlib
import re
import sys


def audit(log: pathlib.Path) -> list[str]:
    t = log.read_text()
    spike_dir = log.parent

    decisions = re.findall(r"^### (D-\d+)", t, re.M)
    questions = re.findall(r"^\| ~*(Q-\d+)~*", t, re.M)
    dec_ids = set(decisions)
    open_q = set(re.findall(r"^\| (Q-\d+) \|", t, re.M))
    closed_q = set(re.findall(r"^\| ~~(Q-\d+)~~", t, re.M))
    all_q = open_q | closed_q

    problems: list[str] = []

    # Referenced decisions must exist.
    for ref in sorted(set(re.findall(r"(?:Superseded by|Answered by) (D-\d+)", t))):
        if ref not in dec_ids:
            problems.append(f"reference to non-existent {ref}")

    # Referenced questions must exist.
    for ref in sorted(set(re.findall(r"\bQ-\d+\b", t))):
        if ref not in all_q:
            problems.append(f"reference to non-existent {ref}")

    # A Tentative decision must be gated by a question that is still open.
    for m in re.finditer(r"^### (D-\d+).*?\n(.*?)(?=\n### D-|\n---)", t, re.M | re.S):
        did, body = m.groups()
        if not re.search(r"\*\*Status:\*\* *Tentative", body):
            continue
        gates = re.findall(r"settled by (Q-\d+)", body)
        if not gates:
            problems.append(f"{did} is Tentative but names no gating question")
        for g in gates:
            if g in closed_q:
                problems.append(
                    f"{did} is still Tentative but its gate {g} is CLOSED "
                    f"— finalise it or re-gate it"
                )

    # Duplicate IDs.
    for ids, label in ((decisions, "decision"), (questions, "question")):
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            problems.append(f"duplicate {label} id {dup}")

    # Spike Index vs files on disk.
    indexed = set(re.findall(r"\]\((\d\d-[a-z0-9-]+\.md)\)", t))
    on_disk = {p.name for p in spike_dir.glob("[0-9][0-9]-*.md")}
    for s in sorted(indexed - on_disk):
        problems.append(f"Spike Index links {s} but the file does not exist")
    for s in sorted(on_disk - indexed):
        problems.append(f"{s} exists on disk but is not in the Spike Index")

    counts = (
        f"decisions {len(dec_ids)} | questions {len(all_q)} "
        f"({len(open_q)} open, {len(closed_q)} closed) | spikes {len(on_disk)}"
    )
    print(counts)
    return problems


def main() -> int:
    log = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "docs/spikes/overall.md")
    if not log.is_file():
        print(f"log not found: {log}", file=sys.stderr)
        return 2
    problems = audit(log)
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("clean — no dangling references, orphaned gates, duplicate ids, or index drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
