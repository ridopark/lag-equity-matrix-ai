"""The baseline guard, over the synthetic universe, as part of the suite.

The real guard needs data/ inputs that are not committed (D-51), so it can only
run on a machine that holds them. This runs the same comparison over the
fabricated universe in tests/fixtures/, which every clone has — so a refactor
that changes pipeline behaviour fails here rather than going unnoticed until
someone remembers to run a script (D-52).

The six candidates are constructed to land on distinct branches: corroborated
on an up signal and on a down one, contradicted, neutral by tie, neutral by
insufficient evidence, and no_assessment. See scripts/make_synthetic.py.
"""

from __future__ import annotations

import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

from capture_baseline import (  # noqa: E402
    COLUMNS,
    SYNTHETIC_BASELINE,
    SYNTHETIC_CLOSES,
    SYNTHETIC_FIRES,
    rows,
)


def test_synthetic_baseline_unchanged():
    with open(SYNTHETIC_BASELINE) as fh:
        expected = list(csv.DictReader(fh))
    actual = rows(SYNTHETIC_CLOSES, SYNTHETIC_FIRES)

    assert len(actual) == len(expected)
    for e, a in zip(expected, actual, strict=True):
        for col in COLUMNS:
            assert str(e[col]) == str(a[col]), (
                f"{a['symbol']} {a['as_of']}: {col} {e[col]!r} -> {a[col]!r}"
            )


def test_synthetic_universe_covers_every_verdict_branch():
    """A guard that only ever sees one branch cannot detect a break in the others."""
    with open(SYNTHETIC_BASELINE) as fh:
        verdicts = {r["verdict"] for r in csv.DictReader(fh)}
    assert verdicts == {"corroborated", "contradicted", "neutral", "no_assessment"}
