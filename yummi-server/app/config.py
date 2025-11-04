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
    thin_runner_log_path: str = Field(default="data/thin-runner-log.txt")
    thin_slice_enabled: bool = Field(default=True)

    # API
    cors_allowed_origins: List[str] = Field(default_factory=lambda: ["*"])
    admin_emails: List[str] = Field(default_factory=list)
    request_max_body_mb: int = Field(default=25)

    # OpenAI
    openai_api_key: str | None = Field(default=None)
    openai_allowed_models: List[str] = Field(default_factory=lambda: ["gpt-4o-mini", "gpt-4o", "o4-mini"])
    openai_default_model: str = Field(default="gpt-4o-mini")

    # Observability
    sentry_dsn: str | None = Field(default=None)
    sentry_traces_sample_rate: float = Field(default=0.0)

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
