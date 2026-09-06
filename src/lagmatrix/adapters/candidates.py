"""Where candidates come from — the one seam between corroboration and scanning.

Everything downstream of `ingest` is identical in both modes, so only the source
needs a second implementation.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Protocol

from lagmatrix.domain.models import Candidate


class CandidateSource(Protocol):
    def candidates(self, as_of: date) -> list[Candidate]: ...


class ExternalSignals:
    """Candidates are signals produced upstream.

    Reads the extract written by `scripts/extract_fires.py` (one row per distinct
    signal, tenant-deduplicated). Pointed at a historical extract this is also
    the replay path for evaluation.
    """

    def __init__(self, path: str | Path = "data/fires.csv"):
        self.path = Path(path)

    def candidates(self, as_of: date | None = None) -> list[Candidate]:
        out = []
        with self.path.open() as fh:
            for row in csv.DictReader(fh):
                if not row["posted_at"] or not row["direction"]:
                    continue
                d = date.fromisoformat(row["posted_at"][:10])
                if as_of is not None and d != as_of:
                    continue
                out.append(
                    Candidate(
                        symbol=row["ticker"],
                        direction=row["direction"],
                        as_of=d,
                        origin="external",
                    )
                )
        return out


class MarketScan:
    """Deferred mode: originate candidates by scanning the universe (D-23).

    Not implemented. When it is, it runs `shocks.standardised_moves` over the
    whole universe and traverses each shocked leader to its laggers, emitting one
    Candidate per lagger with `origin="scan"` — keeping scan-specific logic here
    rather than in the graph.
    """

    def candidates(self, as_of: date) -> list[Candidate]:
        raise NotImplementedError
