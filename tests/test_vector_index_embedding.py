"""PHASE-2 (PLAN-2026-09-09-homelab-deploy): the fastembed swap for
`NewsIndex`'s query-time encoder.

Two properties, deliberately not the same kind of test:

- `test_newsindex_does_not_import_torch` and
  `test_sentence_transformers_absent_from_the_tree` pin a *dependency*
  property. They are red today: `vector.py:16` still does
  `from sentence_transformers import SentenceTransformer` at module scope,
  which pulls in torch transitively.
- `test_newsindex_query_embedding_matches_stored_vectors` pins a *behavioural*
  property -- re-embedding the exact text `scripts/load_vectors.py:107`
  embedded at ingestion time reproduces the vector already stored for it in
  the live `article` collection. It passes today, because
  sentence-transformers is the encoder that produced the stored corpus in
  the first place. It is a regression guard for the encoder swap, not
  evidence of a defect: it must keep passing, unchanged, once `vector.py`
  moves to fastembed (TASK-2.2), which is the whole point of pinning it
  before that change happens.

  This does *not* go through `NewsIndex.search()`. An earlier draft did, and
  measured only 0.89-0.99 cosine similarity against the fixture's stored
  vectors -- not a tolerance problem but a text mismatch: `search()` embeds
  a bare query string, while the stored corpus was built from
  `headline + ". " + summary[:600]` (`load_vectors.py:107`), a different,
  longer text. Reproducing the *ingestion* text and comparing to the
  *ingestion-time* vector is a different, narrower claim than "a live
  search query lands near its target," and only the former is what this
  test can honestly pin without a real ArangoDB and a real ranking to judge
  relevance against.

No test here touches a live ArangoDB or the network.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import numpy as np
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
THIS_FILE = pathlib.Path(__file__).resolve()
FIXTURE_PATH = pathlib.Path(__file__).resolve().parent / "fixtures" / "embedding-reference.json"

# Split so this file's own source text never contains the literal names it
# is asserting are gone from the tree -- otherwise the grep-style test below
# would find itself and could never pass, even once vector.py is fixed.
_BANNED_NAMES = ("sentence" + "_transformers", "Sentence" + "Transformer")


def test_newsindex_does_not_import_torch():
    """Red today. Importing `lagmatrix.adapters.vector` must not leave torch
    (or sentence_transformers) resident in the process.

    Run in a subprocess, not in-process: another test in the same pytest
    session may already have imported `lagmatrix.adapters.vector` (or
    `sentence_transformers` directly, e.g. `test_vector_index.py`'s `index`
    fixture), and an in-process `sys.modules` check after that would keep
    passing regardless of what `vector.py` itself does -- a clean
    interpreter is the only way this actually falsifies.

    Falsifies if: `vector.py`, directly or via a transitive import such as
    `sentence_transformers`, causes `torch` or `sentence_transformers` to
    appear in `sys.modules` after being imported alone.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "import lagmatrix.adapters.vector\n"
         "import sys\n"
         "bad = [m for m in ('torch', 'sentence_transformers') if m in sys.modules]\n"
         "assert not bad, bad\n"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_sentence_transformers_absent_from_the_tree():
    """Red today. `src/`, `scripts/` and `tests/` must contain no reference
    to sentence-transformers once the swap is done -- the plan's own
    completion criterion (`grep -rn "sentence_transformers\\|SentenceTransformer"
    src/ scripts/ tests/` returns nothing).

    Restricted to `*.py` files: those three directories hold no other
    committed source that could plausibly reference a Python import name
    (confirmed by running the equivalent grep over all file types before
    writing this test -- the only hits are `.py` files).

    Falsifies if: any `.py` file under those directories (other than this
    one, which must describe the banned names to explain itself) contains
    either banned name. Today this fails on exactly four files:
    `src/lagmatrix/adapters/vector.py`, `scripts/load_vectors.py`,
    `scripts/capture_showcase.py`, `tests/test_vector_index.py`.
    """
    hits = []
    for rel_dir in ("src", "scripts", "tests"):
        for path in (REPO_ROOT / rel_dir).rglob("*.py"):
            if path.resolve() == THIS_FILE:
                continue
            text = path.read_text()
            for name in _BANNED_NAMES:
                if name in text:
                    hits.append(f"{path.relative_to(REPO_ROOT)}: {name}")
    assert hits == [], hits


def _embed(model, text: str) -> list[float]:
    """The one place in this file allowed to know both of TASK-2.2's
    encoder call-conventions -- sentence-transformers' `.encode` today,
    fastembed's `.embed` after the swap -- because this test must keep
    passing, unedited, on both sides of it (`tdd-green` never edits a
    test), and `NewsIndex` exposes no third, stable public method for
    turning arbitrary text into a vector: `search()` does that, plus
    ranking/filtering/AQL execution this test has no business exercising
    for a pure encoder-reproduces-ingestion check.
    """
    if hasattr(model, "encode"):
        return model.encode([text], normalize_embeddings=True)[0].tolist()
    return next(model.embed([text])).tolist()


def _cosine(a: list[float], b: list[float]) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _load_fixture() -> list[dict]:
    """The fixture is real Alpaca/Benzinga `{_key, headline, summary,
    embedding}` rows, captured read-only and gitignored (see the .gitignore
    comment next to its path) -- this repo is public, that corpus is not
    synthetic test data. `summary` was added to the original capture
    specifically so this file's ingestion text (`headline + ". " +
    summary[:600]`) can be reconstructed exactly -- headline alone is not
    what was embedded when the corpus was built.

    Fails outright when the file is missing, on every machine, including
    CI: this fixture has no legitimate "not there yet" state analogous to
    "no ArangoDB tunnel from this laptop" (the case `conftest.py`'s
    `_unavailable` skips by default for). It is a one-time, already-done
    capture that either exists in a checkout or does not; Q-43 was nine
    tests silently skipping for weeks specifically because "missing"
    resolved to a quiet pass-through instead of a visible failure. Stated
    plainly since it changes what CI observes: `.github/workflows/ci.yml`
    never populates this file, so `test_newsindex_query_embedding_matches_
    stored_vectors` will fail (not skip, not pass) on every CI run until
    something supplies it there -- a decision for whoever owns that
    pipeline, not silently defaulted here.
    """
    if not FIXTURE_PATH.exists():
        pytest.fail(
            f"{FIXTURE_PATH} is missing. This is a committed-format, "
            "gitignored fixture of real stored embeddings -- regenerate it "
            "read-only from the live `article` collection (see the "
            "surrounding comment in .gitignore) rather than skipping this "
            "test."
        )
    return json.loads(FIXTURE_PATH.read_text())


def test_newsindex_query_embedding_matches_stored_vectors():
    """Guard, passing today: pins the property this whole deployment plan
    depends on -- an encoder swap must not make the 47,640 already-indexed
    articles unsearchable. Reproduces `load_vectors.py:107`'s exact
    ingestion text (`headline + ". " + summary[:600]` -- the `600` and the
    `". "` join are load-bearing; changing either there silently
    invalidates every stored vector's reproducibility) for each fixture
    article, re-embeds it with `NewsIndex`'s own model (via `_embed`, not
    `search()` -- see the module docstring for why), and compares it to
    that same article's already-stored embedding.

    Threshold: cosine similarity within 1e-4 of 1.0, not a one-sided
    `>= 0.999`. Measured directly (this exact reproduction, in this repo,
    against the current encoder): 52895666 -> 1.000005, 52895858 ->
    0.999997, 52896531 -> 1.000000, 52896559 -> 0.999999, 52896680 ->
    1.000000, 52896689 -> 0.999999, 52896790 -> 1.000002, 52896823 ->
    0.999997 -- i.e. D-101's headline "1.000000 to six decimals" including
    values fractionally *above* 1.0 (stored vectors are float32, this
    comparison is float64), so `cos <= 1.0` would be a bug and a one-sided
    `>= 0.999` would silently tolerate ten times more drift on the high
    side than is ever actually observed. 1e-4 sits two to three orders of
    magnitude above every measured value here while still failing hard on
    a materially different encoder -- the headline-only mismatch this test
    replaced was off by 0.006-0.11, nowhere near this margin.

    Falsifies if: the code path that produces `NewsIndex`'s embeddings
    stops reproducing this exact ingestion-time vector -- e.g. a future
    dependency bump that changes fastembed's build of `all-MiniLM-L6-v2`,
    or a normalization step inconsistent with how the stored corpus was
    built.
    """
    fixture = _load_fixture()
    from lagmatrix.adapters.vector import NewsIndex

    index = NewsIndex(db=None)

    for article in fixture:
        text = article["headline"] + ". " + article["summary"][:600]
        vector = _embed(index._model, text)
        similarity = _cosine(vector, article["embedding"])
        assert abs(similarity - 1.0) <= 1e-4, (
            f"{article['_key']!r}: cosine similarity {similarity:.6f}, "
            "expected within 1e-4 of 1.0"
        )
