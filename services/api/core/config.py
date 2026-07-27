from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── API ────────────────────────────────────────────────────────────────────
    API_ENV: str = "development"
    API_LOG_LEVEL: str = "info"

    # ── Database ───────────────────────────────────────────────────────────────
    # SQLAlchemy async DSN — asyncpg driver
    DATABASE_URL: str = "postgresql+asyncpg://vibeforge:vibeforge_dev_secret@postgres:5432/vibeforge"
    TENANT_SETTING_KEY: str = "app.current_tenant_id"

    # ── Redis ──────────────────────────────────────────────────────────────────
    # Full DSN used by redis.asyncio (pub/sub)
    REDIS_URL: str = "redis://:redis_dev_secret@redis:6379/0"
    # Explicit params used by Arq (doesn't parse full DSN reliably)
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "redis_dev_secret"

    # ── Keycloak ───────────────────────────────────────────────────────────────
    # Internal Docker network address — API → Keycloak container-to-container
    KEYCLOAK_JWKS_URL: str = (
        "http://keycloak:8080/realms/vibeforge/protocol/openid-connect/certs"
    )
    KEYCLOAK_REALM: str = "vibeforge"

    # ── Seed ───────────────────────────────────────────────────────────────────
    SEED_TENANT_ID: str = "00000000-0000-0000-0000-000000000001"


@lru_cache
def get_settings() -> Settings:
    return Settings()
