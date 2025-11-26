from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List


class Settings(BaseSettings):
    # Core
    app_name: str = Field(default="yummi-server")
    environment: str = Field(default="dev")  # dev|staging|prod
    log_json: bool = Field(default=True)
    log_level: str = Field(default="INFO")

    # Auth (Clerk)
    clerk_issuer: str | None = Field(default=None)
    clerk_jwks_url: str | None = Field(default=None)
    clerk_audience: str | None = Field(default=None)
    auth_disable_verification: bool = Field(default=False)

    # Data
    database_url: str | None = Field(default=None)
    redis_url: str | None = Field(default=None)
    catalog_path: str | None = Field(default="resolver/catalog.json")
    meals_manifest_path: str | None = Field(default="resolver/meals/meals_manifest.json")
    tags_manifest_path: str | None = Field(default="data/tags/defined_tags.json")
    thin_runner_log_path: str = Field(default="data/thin-runner-log.txt")
    thin_slice_enabled: bool = Field(default=True)

    # API
    cors_allowed_origins: List[str] = Field(default_factory=lambda: ["*"])
    admin_emails: List[str] = Field(default_factory=list)
    request_max_body_mb: int = Field(default=25)

    # OpenAI
    openai_api_key: str | None = Field(default=None)
    openai_allowed_models: List[str] = Field(
        default_factory=lambda: ["gpt-4o-mini", "gpt-4o", "o4-mini", "gpt-5", "gpt-5-mini"]
    )
    openai_default_model: str = Field(default="gpt-5-mini")
    openai_exploration_model: str = Field(default="gpt-5-mini")
    openai_exploration_temperature: float = Field(default=0.15)
    openai_exploration_top_p: float | None = Field(default=None)
    openai_exploration_reasoning_effort: str = Field(default="low")
    openai_exploration_max_output_tokens: int = Field(default=1000)
    exploration_stream_timeout_seconds: int = Field(default=15, ge=1, le=60)
    exploration_candidate_limit: int = Field(default=10)
    exploration_meal_count: int = Field(default=15)
    openai_recommendation_model: str = Field(default="gpt-5-mini")
    openai_recommendation_top_p: float | None = Field(default=None)
    openai_recommendation_reasoning_effort: str = Field(default="low")
    openai_recommendation_max_output_tokens: int = Field(default=3000)
    recommendation_candidate_limit: int = Field(default=100)
    recommendation_meal_count: int = Field(default=10)
    recommendation_stream_timeout_seconds: int = Field(default=20, ge=5, le=120)
    openai_shopping_list_model: str = Field(default="gpt-5-mini")
    openai_shopping_list_top_p: float | None = Field(default=None)
    openai_shopping_list_reasoning_effort: str = Field(default="low")
    openai_shopping_list_max_output_tokens: int = Field(default=12000)
    openai_request_timeout_seconds: int = Field(default=90, ge=30, le=300)

    # Observability
    sentry_dsn: str | None = Field(default=None)
    sentry_traces_sample_rate: float = Field(default=0.0)

    # PayFast
    payfast_merchant_id: str | None = Field(default=None)
    payfast_merchant_key: str | None = Field(default=None)
    payfast_passphrase: str | None = Field(default=None)
    payfast_notify_url: str | None = Field(default=None)
    payfast_return_url: str | None = Field(default=None)
    payfast_cancel_url: str | None = Field(default=None)
    payfast_mode: str = Field(default="sandbox")
    payfast_pdt_token: str | None = Field(default=None)
    payfast_return_deeplink: str | None = Field(default=None)
    payfast_cancel_deeplink: str | None = Field(default=None)
    payfast_skip_remote_validation: bool = Field(default=False)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @field_validator("cors_allowed_origins", "admin_emails", mode="before")
    @classmethod
    def _csv_to_list(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
