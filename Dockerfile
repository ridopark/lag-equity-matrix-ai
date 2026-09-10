# One image, two entrypoints. The web Deployment runs `scripts/serve.py`; the
# ingest CronJob overrides CMD to `scripts/daily_ingest.py`. They differ in
# which Secret is mounted and whether they write the data volume -- both
# properties of the manifest, not of the image -- so splitting them would buy
# a second build and a second tag stream for nothing.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    FASTEMBED_CACHE_PATH=/opt/fastembed_cache

COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv

WORKDIR /app

# UV_NO_CACHE above is worth 454MB. With UV_LINK_MODE=copy, uv downloads each
# wheel into ~/.cache/uv and then *copies* it into the venv, so both survive in
# the same layer and the image pays for every dependency twice. Nothing reads
# that cache at runtime. Measured: 1.17GB -> 716MB.
#
# `--frozen` is load-bearing, not decoration: uv.lock carries 1,658 sha256
# hashes across 130 packages, and a plain `uv sync` or `pip install -e .`
# re-resolves and discards every one of them.
#
# Two-stage sync, unlike the reference repo's one-stage: `lagmatrix` is a real
# src-layout installable, so a single `uv sync` would need the source tree and
# any code edit would bust the dependency layer. This installs third-party
# deps against a layer only the lockfile can invalidate.
# README.md comes along because pyproject.toml:5 declares `readme = "README.md"`,
# and the build backend reads it when installing the local project below.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/opt/venv/bin:${PATH}"

# Warm the ONNX weights into their own layer, ahead of source, so editing code
# does not re-download the model. Same name vector.py and load_vectors.py use;
# the 47,829 stored vectors live in this model's space (D-101), so it is not
# interchangeable.
RUN mkdir -p "$FASTEMBED_CACHE_PATH" \
    && python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')" \
    && chmod -R a+rX "$FASTEMBED_CACHE_PATH"

COPY src/ ./src/
RUN uv sync --frozen --no-dev

COPY scripts/ ./scripts/
COPY data/excluded-etfs.csv ./data/excluded-etfs.csv
COPY tests/fixtures/synthetic-closes.parquet tests/fixtures/synthetic-fires.csv ./tests/fixtures/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# `--host 0.0.0.0` is required: serve.py defaults to 127.0.0.1, which would
# leave the Service unable to reach the pod at all.
ENTRYPOINT ["python"]
CMD ["scripts/serve.py", "--host", "0.0.0.0", "--port", "8000", "--allow-real"]
