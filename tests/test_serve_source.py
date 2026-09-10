"""RED: `serve.load`'s `source` argument is not validated, so a malformed or
unrecognised value either raises a raw internal exception or, worse, is
silently answered with synthetic data.

Two distinct defects, both reproduced directly against `serve.load` before
writing these tests (not inferred):

    serve.load('leader:PANW', None, '2026-09-04')       -> 6 candidates (fine)
    serve.load('leader:', None, '2026-09-04')            -> KeyError: "None of
        [Index([''], dtype='str', name='symbol')] are in the [columns]"
    serve.load('leader:A B', ...)                        -> same KeyError shape
    serve.load('leader:../../etc', ...)                  -> same KeyError shape
    serve.load('leader:ZZZZZZ', ...)                     -> same KeyError shape
    serve.load('leader', None, '2026-09-04')             -> 6 SYNTHETIC candidates
    serve.load('nonsense', None, '2026-09-04')           -> 6 SYNTHETIC candidates
    serve.load('', None, '2026-09-04')                   -> 6 SYNTHETIC candidates

`scripts/serve.py`'s `load()` ends with an unguarded fallback:

    closes = pd.read_parquet(SYNTHETIC_CLOSES)
    return closes, ExternalSignals(SYNTHETIC_FIRES).candidates(), frozenset()

so any string that is not `'scan'`, `'real'`, or does not start with
`'leader:'` reaches that line and gets invented data back -- the same failure
class already logged as D-100 (a wrong number published quietly) and Q-43 (a
test that looks healthy while verifying nothing): a caller who mistypes
`?source=leader` (no colon) or asks for `?source=nonsense` gets a *200* full
of synthetic candidates with no signal that anything was misunderstood.

The `KeyError` on a malformed or unknown `leader:` symbol comes from one line
in `CoMovementFollowers._leader_shock_z` (`src/lagmatrix/adapters/candidates.py`):
`shocks.standardised_moves` does `returns[symbols]` where `symbols = [leader]`,
which raises `KeyError` verbatim from pandas whenever `leader` is not a column
of `closes` -- whether because it is empty, contains characters no ticker has,
or is simply a symbol the price file does not carry. All four shapes are
treated as one behaviour here: "a `leader:` source that does not resolve to a
tradeable column must raise a typed, catchable error naming the value", since
the fix is the same validation step (check membership in `closes.columns`
before calling into `CoMovementFollowers`) regardless of *why* the symbol
doesn't resolve.

`ValueError` is the target type for both defects (unresolvable `leader:`
symbol and unrecognised source), matching `test_serve_params.py`'s
`parse_bounded`, which already raises `ValueError` for the handler's
`except Exception as e: self.send_error(400, ...)`-shaped callers to catch
uniformly -- introducing a second exception type here for what the caller
does with it identically would be an unjustified extra concept.

Tests only, per this phase's scope; `src/` and `scripts/` are untouched.
`'leader:PANW'` needs `data/bars-10y.parquet`, which IS present on this
machine (confirmed via `ls data/` before writing this file) so that test
exercises the real path rather than skipping -- see Q-43 in
`docs/spikes/overall.md` for why a test that skips silently on a machine
where the dependency exists is exactly the failure this project already got
burned by once.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from conftest import require_local_file

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

# The date D-95/D-100's co-movement work was measured on; also the one used
# to reproduce the defects above.
AS_OF = "2026-09-04"


@pytest.fixture(autouse=True)
def _scripts_on_path():
    sys.path.insert(0, str(SCRIPTS))
    yield
    sys.path.remove(str(SCRIPTS))


# ---------------------------------------------------------------------------
# Guard: recognised sources must keep working. Written first so a later fix
# that makes validation too aggressive is caught here, not just in the new
# rejection tests below.
# ---------------------------------------------------------------------------

def test_synthetic_source_returns_non_empty_synthetic_candidates():
    """`'synthetic'` is the default, credential-free source and must keep
    returning the committed fixture's candidates -- not raise, and not
    silently return an empty list.

    Falsifies if: this raises, or `cands` is empty.
    """
    import serve

    serve.ALLOW_REAL = False
    closes, cands, universe = serve.load("synthetic", None, AS_OF)
    assert len(cands) > 0
    assert universe == frozenset()
    assert closes.shape[0] > 0


def test_leader_source_with_known_symbol_returns_non_empty_candidates():
    """The exact call the report opened with: a real, known leader must still
    resolve to its followers once validation exists -- the fix for malformed
    input must not also break the valid case.

    Falsifies if: this raises, or `cands` is empty.
    """
    require_local_file("data/bars-10y.parquet", "real long bars, vendor data")
    import serve

    serve.ALLOW_REAL = True
    closes, cands, universe = serve.load("leader:PANW", None, AS_OF)
    assert len(cands) > 0
    assert universe == frozenset({"PANW"})


def test_synthetic_must_be_an_explicitly_recognised_source_not_a_fallthrough():
    """Today `'synthetic'` reaches its data through the exact same unguarded
    fall-off-the-end return as `'nonsense'` does (`scripts/serve.py:175-176`)
    -- there is no `if source == "synthetic"` anywhere in `load()`. So this
    passing does not yet prove `'synthetic'` is a *recognised* source; it only
    proves the current bug is symmetric.

    `'synthetic '` (one trailing space) is the adversarial probe that tells
    the two apart: it does not start with `'scan'`, `'real'`, or `'leader:'`
    either, so it reaches the identical fallback `'nonsense'` does and
    silently returns synthetic data today. Once unrecognised sources raise,
    a fix that merely blocklists the specific bad strings this file happens
    to exercise (leaving the bare fallback in place) would still let
    `'synthetic '` through -- only an explicit, exact match on `'synthetic'`
    closes that gap.

    Falsifies if: this does not raise `ValueError`.
    """
    import serve

    serve.ALLOW_REAL = False
    with pytest.raises(ValueError):
        serve.load("synthetic ", None, AS_OF)


# ---------------------------------------------------------------------------
# `leader:` with a symbol that cannot resolve to a column of `closes` --
# empty, containing characters no ticker has, or simply absent from the
# price file. All raise `KeyError` today; all must raise `ValueError` instead.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("leader_part", ["", "A B", "../../etc", "ZZZZZZ"])
def test_leader_source_with_unresolvable_symbol_raises_value_error_not_keyerror(leader_part):
    """An empty, malformed, or simply unknown leader symbol must not surface
    as a raw `KeyError` from a pandas column lookup two layers down.

    Falsifies if: `serve.load` raises `KeyError` (or anything other than
    `ValueError`) for any of these four `leader:` values.
    """
    import serve

    serve.ALLOW_REAL = True
    with pytest.raises(ValueError):
        serve.load(f"leader:{leader_part}", None, AS_OF)


def test_unknown_leader_symbol_error_names_the_symbol():
    """A caller acting on the error needs to know *which* symbol was
    rejected -- pins that the message carries it, not just that some
    `ValueError` was raised.

    Falsifies if: this does not raise `ValueError`, or `'ZZZZZZ'` is absent
    from its message.
    """
    import serve

    serve.ALLOW_REAL = True
    with pytest.raises(ValueError, match="ZZZZZZ"):
        serve.load("leader:ZZZZZZ", None, AS_OF)


def test_leader_symbol_rejected_for_parity_with_the_other_endpoints_isalnum_guard():
    """`/followers`, `/network` and `/graph` each reject a non-alphanumeric
    `symbol` query parameter with `.isalnum()` before it reaches any
    computation (`scripts/serve.py:553-554`, `:565-566`, `:578-579`). `/run`'s
    `source=leader:<symbol>` embeds the same kind of value with no equivalent
    guard anywhere in `load()`'s leader-mode branch -- the inconsistency, not
    merely "this string happens to not be a column", is the defect: shape
    validation for a leader symbol should not depend on it accidentally
    missing the price file too.

    `'AB;DROP'` is non-empty, non-alnum, and (like the other malformed cases
    above) also not a real column -- both properties fail today with the same
    raw `KeyError`, and the fix must reject it for being the wrong shape, in
    line with the other three handlers, not merely because it happens to be
    absent from `closes.columns`.

    Falsifies if: this does not raise `ValueError`.
    """
    import serve

    serve.ALLOW_REAL = True
    with pytest.raises(ValueError):
        serve.load("leader:AB;DROP", None, AS_OF)


# ---------------------------------------------------------------------------
# A `source` that names no known mode at all: must not fall through to the
# unguarded synthetic default.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_source", ["nonsense", "leader", ""])
def test_unrecognised_source_raises_value_error_not_synthetic_fallback(bad_source):
    """`'leader'` with no colon, a typo like `'nonsense'`, and an empty
    string are all sources `load()` does not recognise. Today each one falls
    through every `if` and returns the synthetic fixture as if it were what
    was asked for -- this is the D-100/Q-43 failure class (a wrong answer
    delivered quietly): they must raise instead.

    Falsifies if: `serve.load` returns instead of raising for any of these
    three values, regardless of `ALLOW_REAL`.
    """
    import serve

    serve.ALLOW_REAL = True
    with pytest.raises(ValueError):
        serve.load(bad_source, None, AS_OF)


def test_unrecognised_source_error_names_the_bad_source():
    """As above: pins that the message names the actual bad value, not a
    generic "invalid source" with nothing to act on.

    Falsifies if: this does not raise `ValueError`, or `'nonsense'` is absent
    from its message.
    """
    import serve

    serve.ALLOW_REAL = True
    with pytest.raises(ValueError, match="nonsense"):
        serve.load("nonsense", None, AS_OF)
