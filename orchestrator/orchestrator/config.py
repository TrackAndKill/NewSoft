from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    database_url: str = "postgresql+psycopg://newsoft:changeme@localhost:5432/newsoft"
    orchestrator_port: int = 8000
    dry_run: bool = True
    daily_spend_cap_usd: float = 5.00
    system_active: bool = True
    brave_api_key: str = ""
    brave_search_api_key: str = ""

    model_opus: str = "claude-opus-4-7"
    model_sonnet: str = "claude-sonnet-4-6"
    model_haiku: str = "claude-haiku-4-5-20251001"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
