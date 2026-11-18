"""Environment-based configuration. Nothing secret is hardcoded; see `.env.example`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"), env_prefix="FLEETPILOT_", extra="ignore"
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://fleetpilot:fleetpilot@localhost:5432/fleetpilot"
    dataset_path: str = str(REPO_ROOT / "data" / "device-telemetry-dataset.ndjson")

    # --- LLM -------------------------------------------------------------------
    # "deterministic" needs no key and is what tests/evals use by default.
    llm_provider: Literal["deterministic", "anthropic", "openai"] = "deterministic"
    llm_model: str | None = None  # provider default if unset
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2  # SDK-level retries with backoff (429 / 5xx / connection errors)
    # Which agent stages use the LLM: "all" = plan + review + respond (LLM mode);
    # "plan" = plan + review only, answers composed deterministically (hybrid mode).
    llm_stages: Literal["all", "plan"] = "all"
    llm_effort: str | None = "low"  # Anthropic output_config.effort; empty to omit (older models)
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")

    # --- Agent -----------------------------------------------------------------
    agent_max_tool_calls: int = 8
    agent_max_replans: int = 2  # bounded re-planning rounds (error repair or LLM-requested follow-ups)
    tool_timeout_seconds: float = 10.0

    # --- Actions / approval -----------------------------------------------------
    approval_ttl_hours: int = 72
    require_distinct_approver: bool = False

    # --- API -------------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = 60
    # Demo bearer tokens are derived from this secret (HMAC). Change it in any real deployment.
    auth_secret: str = "change-me-in-production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
