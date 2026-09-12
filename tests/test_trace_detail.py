"""RED: `capture_trace.detail` drops `quant_perspective` on the floor.

`scripts/capture_trace.py::detail(node, upd)` renders a human-readable
summary of each graph node update for the live scan UI (`scripts/serve.py:
280-281` emits it as the `detail` field of every event). It branches on
`graph_retriever`, `leader_state`, `vector_retriever`, `context_fusion` and
`assessor` -- there is no branch for `quant_perspective`, so that node falls
through to the final `return key, ""` and the UI shows an empty string. The
node's `QuantPerspective` output -- including the relatedness fields
(`sector_match_pct`, `comention_weak_count`, `comention_strong_count`) added
for D-135/D-136 -- is computed and never surfaced anywhere.
"""

from __future__ import annotations

import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from capture_trace import detail  # noqa: E402

from lagmatrix.domain.models import QuantPerspective  # noqa: E402

KEY = "AAPL|2024-01-05"


def _qp(**overrides) -> QuantPerspective:
    """A QuantPerspective with sane defaults for the fields this test does
    not care about, overridden by whatever the caller is asserting on."""
    fields = dict(
        n_edges=4,
        median_ci_width=0.2,
        duplicate_count=0,
        split_half_sign_agree_pct=75.0,
        split_half_min_abs=0.3,
        candidate_is_etf=False,
        note="4 correlation edge(s) over a 60-session trailing window",
        sector_match_pct=50.0,
        comention_weak_count=2,
        comention_strong_count=1,
    )
    fields.update(overrides)
    return QuantPerspective(**fields)


def test_detail_renders_quant_perspective_relatedness():
    """Falsifies if the summary omits the actual relatedness numbers (e.g. a
    stub that returns a constant string, or the current fall-through to
    `""`), or if it rounds/drops the sector-match percentage or the
    strong/weak co-mention counts rather than reporting them."""
    upd = {"quant_by_key": {KEY: _qp(
        n_edges=4, sector_match_pct=50.0,
        comention_strong_count=1, comention_weak_count=2,
    )}}

    _, summary = detail("quant_perspective", upd)

    assert "50" in summary
    assert "1" in summary
    assert "2" in summary


def test_detail_quant_perspective_says_when_relatedness_is_unavailable():
    """The `arango_topology is None` case: `sector_match_pct`,
    `comention_weak_count` and `comention_strong_count` are all `None`, not
    zero. Falsifies if the summary prints a bare "0" or "None" as though
    relatedness had been measured and found nothing -- that is a different
    state from "not measured at all" (the exact D-135/D-136 confusion this
    line of work exists to avoid) -- or if it silently omits `n_edges`."""
    upd = {"quant_by_key": {KEY: _qp(
        n_edges=4, sector_match_pct=None,
        comention_weak_count=None, comention_strong_count=None,
    )}}

    _, summary = detail("quant_perspective", upd)

    assert "4" in summary  # n_edges must still be reported
    assert "0" not in summary
    assert "None" not in summary
    # some explicit marker that relatedness was not measured, not just absent
    markers = ("unavailable", "disabled", "not measured", "n/a")
    assert any(word in summary.lower() for word in markers)


def test_detail_returns_empty_for_an_unknown_node():
    """Regression guard: a node name with no branch must still fall through
    to `(key, "")` rather than raising. Should already be green -- this pins
    the fall-through so a future branch addition can't accidentally remove
    it for genuinely unhandled nodes."""
    key, summary = detail("some_unhandled_node", {"candidate": None})

    assert summary == ""
