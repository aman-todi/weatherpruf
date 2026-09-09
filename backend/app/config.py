"""Application settings, loaded from the environment (see .env.example)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Supabase -----------------------------------------------------------
    # e.g. https://abcdefgh.supabase.co — used to derive the JWKS URL and the
    # expected token issuer, and by the admin client that deletes auth users.
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Legacy HS256 project secret. Supabase projects created before asymmetric
    # signing still use it, and it is what the local test-token helper signs
    # with. Left empty on projects that have migrated to JWKS.
    supabase_jwt_secret: str = ""

    # --- Database -----------------------------------------------------------
    # The application role. Full DML on the user-scoped tables; every request
    # runs inside `set local role authenticated` so RLS still applies.
    database_url: str = ""

    # The dedicated read-only role from db/migrations/0004. SELECT on
    # closet_query_view and nothing else. Kept as a separate secret.
    readonly_database_url: str = ""

    db_pool_min_size: int = 1
    db_pool_max_size: int = 5

    # --- Limits (spec §5) ---------------------------------------------------
    max_items_per_user: int = 200
    max_colors_per_item: int = 5
    max_batch_items: int = 20
    daily_mcp_call_limit: int = 10
    query_result_limit: int = 50
    category_listing_limit: int = 100
    query_statement_timeout_ms: int = 2000

    # --- App ----------------------------------------------------------------
    environment: str = "development"
    public_base_url: str = "http://localhost:8000"

    # Comma-separated rather than a list field: pydantic-settings would
    # otherwise try to JSON-decode the raw env value before any validator runs.
    cors_allow_origins: str = "http://localhost:5173"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def token_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def mcp_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/mcp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
