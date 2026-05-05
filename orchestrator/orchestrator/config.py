from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    database_url: str = "postgresql+psycopg://newsoft:changeme@localhost:5432/newsoft"
    orchestrator_port: int = 8000
    dry_run: bool = True
    daily_spend_cap_usd: float = 5.00
    money_daily_cap_usd: float = 50.00
    system_active: bool = True
    brave_api_key: str = ""
    brave_search_api_key: str = ""

    voyage_api_key: str = ""
    memory_provider: str = "voyage"
    openai_api_key: str = ""

    resend_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    digest_to_email: str = ""
    digest_from_email: str = ""

    porkbun_api_key: str = ""
    porkbun_api_secret: str = ""

    public_dashboard_url: str = "https://firm.profithub.me"
    vm_ipv4: str = ""
    certbot_email: str = ""
    lead_ip_hash_pepper: str = "newsoft-static-fallback-pepper"
    site_base_dir: str = "/var/lib/newsoft/sites"
    site_well_known_dir: str = "/var/lib/newsoft/well-known"
    nginx_site_dir: str = "/etc/nginx/newsoft-sites"

    model_opus: str = "claude-opus-4-7"
    model_sonnet: str = "claude-sonnet-4-6"
    model_haiku: str = "claude-haiku-4-5-20251001"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
