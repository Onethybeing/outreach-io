from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://user:password@localhost/outreach_io"

    anthropic_api_key: str = ""
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

    app_mode: str = "dev"  # "dev" | "prod" — see PLAN.md section 6

    @property
    def is_prod(self) -> bool:
        return self.app_mode == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
