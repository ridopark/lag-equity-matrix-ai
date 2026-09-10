"""Where candidates come from — the one seam between corroboration and scanning.

Everything downstream of `ingest` is identical in both modes, so only the source
needs a second implementation.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from lagmatrix import shocks
from lagmatrix.comovement import comovement_edges
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
                try:
                    ts = (
                        datetime.fromisoformat(row["posted_at"])
                        if len(row["posted_at"]) > 10
                        else None
                    )
                except ValueError:
                    ts = None
                out.append(
                    Candidate(
                        symbol=row["ticker"],
                        direction=row["direction"],
                        as_of=d,
                        as_of_ts=ts,
                        origin="external",
                    )
                )
        return out


class MarketScan:
    """Deferred mode: originate candidates by scanning the universe (D-23).

    Runs `shocks.standardised_moves` over the whole universe (PHASE-1), then
    traverses each shocked leader to its laggers, emitting one Candidate per
    lagger with `origin="scan"` — keeping scan-specific logic here rather than
    in the graph (D-23).
    """

    def __init__(
        self,
        closes,
        topology,
        excluded_symbols=frozenset(),
        trail: int = 60,
        move_win: int = 3,
        sigma: float = 2.0,
        max_hops: int = 2,
    ):
        self.closes = closes
        self.topology = topology
        self.excluded_symbols = excluded_symbols
        self.trail = trail
        self.move_win = move_win
        self.sigma = sigma
        self.max_hops = max_hops

    def shocked_leaders(self, as_of: date) -> dict[str, float]:
        """Symbols whose recent move is >= `sigma` standard deviations, over the
        whole universe (minus `excluded_symbols`, D-62), against a baseline that
        ends strictly before the recent window begins — the literal reading of
        `standardised_moves`'s contract, not `leader_state.py`'s overlapping one
        (see the plan's "Dates: exactly what flows where" section).
        """
        sessions = self.closes.index
        later = sessions[sessions > str(as_of)]
        if len(later) == 0:
            return {}
        ti = sessions.get_loc(later[0])
        if ti < self.move_win + self.trail:
            return {}
        returns = self.closes.pct_change()
        baseline = returns.iloc[ti - self.move_win - self.trail : ti - self.move_win]
        recent = returns.iloc[ti - self.move_win : ti]
        symbols = [s for s in self.closes.columns if s not in self.excluded_symbols]
        moves = shocks.standardised_moves(recent, symbols, self.move_win, baseline)
        return {str(sym): float(z) for sym, z in moves.items() if abs(z) >= self.sigma}

    def candidates(self, as_of: date) -> list[Candidate]:
        """One `Candidate` per unique lagger reached from a shocked leader.

        Leaders are visited largest-`|z|`-first (ties broken by symbol,
        ascending); a lagger reached by more than one leader is claimed by
        whichever leader is visited first under that ordering, which is the
        larger-`|z|` leader by construction (REQ-4). Its shock sign sets the
        candidate's direction (REQ-5). Laggers in `excluded_symbols` are
        dropped (D-62).
        """
        leaders = sorted(self.shocked_leaders(as_of).items(), key=lambda kv: (-abs(kv[1]), kv[0]))
        claims: dict[str, Candidate] = {}
        for leader, z in leaders:
            for edge in self.topology.laggers_of(leader, self.max_hops, as_of):
                if edge.lagger in self.excluded_symbols:
                    continue
                if edge.lagger in claims:
                    continue
                claims[edge.lagger] = Candidate(
                    symbol=edge.lagger,
                    direction="up" if z > 0 else "down",
                    as_of=as_of,
                    origin="scan",
                    origin_leader=leader,
                )
        return sorted(claims.values(), key=lambda c: c.symbol)


class CoMovementFollowers:
    """Scan mode's other seam (D-23): candidates drawn from measured
    co-movement (D-95) instead of the lagger graph -- given one shocked
    leader, emit its co-movement followers with a direction inherited from
    the leader's own shock sign, flipped for a negatively correlated edge.

    This claims only same-day co-movement, which replicates at 0.586 out of
    sample (D-95). It asserts nothing about what a follower does next --
    D-93/D-94 nulled lagged prediction across 2.47M pairs.
    """

    _MOVE_WIN = 3
    _SIGMA = 2.0

    def __init__(
        self,
        closes,
        leader: str,
        trail: int = 250,
        min_abs_corr: float = 0.5,
        excluded_symbols=frozenset(),
    ):
        self.closes = closes
        self.leader = leader
        self.trail = trail
        self.min_abs_corr = min_abs_corr
        self.excluded_symbols = excluded_symbols

    def _leader_shock_z(self, as_of: date) -> float | None:
        """The leader's own standardised move, `MarketScan`'s shocked-leader
        convention (move_win=3, sigma=2.0) applied to this one symbol --
        `None` if the leader never cleared threshold on `as_of`.
        """
        sessions = self.closes.index
        later = sessions[sessions > str(as_of)]
        if len(later) == 0:
            return None
        ti = sessions.get_loc(later[0])
        if ti < self._MOVE_WIN + self.trail:
            return None
        returns = self.closes.pct_change()
        baseline = returns.iloc[ti - self._MOVE_WIN - self.trail : ti - self._MOVE_WIN]
        recent = returns.iloc[ti - self._MOVE_WIN : ti]
        moves = shocks.standardised_moves(recent, [self.leader], self._MOVE_WIN, baseline)
        if self.leader not in moves.index:
            return None
        z = float(moves[self.leader])
        return z if abs(z) >= self._SIGMA else None

    def candidates(self, as_of: date) -> list[Candidate]:
        """One `Candidate` per co-movement follower of `self.leader`, or `[]`
        if the leader itself never shocked on `as_of`.
        """
        z = self._leader_shock_z(as_of)
        if z is None:
            return []
        leader_direction = "up" if z > 0 else "down"
        edges = comovement_edges(
            self.closes,
            as_of,
            trail=self.trail,
            min_abs_corr=self.min_abs_corr,
            exclude=self.excluded_symbols,
        )
        out = []
        for edge in edges:
            if edge.a == self.leader:
                follower = edge.b
            elif edge.b == self.leader:
                follower = edge.a
            else:
                continue
            if edge.corr > 0:
                direction = leader_direction
            else:
                direction = "down" if leader_direction == "up" else "up"
            out.append(
                Candidate(
                    symbol=follower,
                    direction=direction,
                    as_of=as_of,
                    origin="comovement",
                    origin_leader=self.leader,
                )
            )
        return sorted(out, key=lambda c: c.symbol)
