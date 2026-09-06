"""Runtime configuration, loaded from environment / .env (see .env.example)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All external endpoints and tunables live here. Nothing else reads os.environ."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="LAGMATRIX_")

    # ArangoDB — structured market topology (equities, correlation/lag edges)
    arango_url: str = "http://localhost:8529"
    arango_db: str = "lagmatrix"
    arango_user: str = "root"
    arango_password: str = ""

    # Vector store — unstructured news / earnings transcripts
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "lagmatrix_news"

    # Market data feed
    market_api_key: str = ""
    market_api_secret: str = ""

    # LLM
    anthropic_api_key: str = ""
    model: str = "claude-opus-5"

    # Candidate source: "external" (score an upstream signal) or "scan"
    # (originate candidates by sweeping the universe — not yet implemented).
    candidate_source: str = "external"

    # Shock detection tunables (daily bars — see D-15)
    shock_sigma: float = 3.0
    shock_lookback_days: int = 60      # trailing window for the volatility baseline
    signal_horizon_days: int = 10      # how far ahead a lagger prediction reaches
    max_lag_hops: int = 2


def load_settings() -> Settings:
    raise NotImplementedError
