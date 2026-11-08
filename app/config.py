from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    database_url: str = "sqlite:///./expense_demo.db"
    session_secret: str = ""
    cookie_secure: bool = False

    # 演示默认放宽；生产按网关/租户调低。
    rate_limit_login: str = "100/minute"
    rate_limit_ask: str = "100/minute"
    rate_limit_stream: str = "100/minute"


@lru_cache
def get_settings() -> Settings:
    return Settings()
