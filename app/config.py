from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://user:password@localhost/outreach_io"

    # Bootstrap secrets (PLAN.md §7): these never live in the vault.
    vault_master_key: str = ""
    session_secret: str = ""
    initial_admin_email: str = ""
    google_login_client_id: str = ""
    google_login_client_secret: str = ""
    public_base_url: str = "http://localhost:8000"
    # Shared secret for scheduler-triggered endpoints (/internal/*). Empty = those endpoints are off.
    internal_task_token: str = ""
    # LLM-as-judge evals after each discovery run and draft (extra Groq tokens). PLAN.md §11.
    auto_evals: bool = True
    # File storage for CVs and dev-mode .eml files: "local" (development) or "gcs" (Cloud Run,
    # whose disk is wiped on every restart).
    storage_backend: str = "local"
    local_storage_dir: str = "storage"
    gcs_bucket: str = ""

    # Provider keys: read only to seed the vault on first start.
    groq_api_key: str = ""
    tavily_api_key: str = ""
    apollo_api_key: str = ""
    brightdata_api_key: str = ""

    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_sender_address: str = ""

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    app_mode: str = "dev"  # "dev" | "prod" — see PLAN.md §6

    @property
    def is_prod(self) -> bool:
        return self.app_mode == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
