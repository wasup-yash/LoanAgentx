from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Traceable Omni-Channel Loan Agent"
    environment: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://loan_agent:loan_agent@localhost:5432/loan_agent"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_mock: bool = False
    openai_api_key: str | None = None

    core_banking_url: str | None = None

    data_dir: str = "./data"
    max_attachment_bytes: int = 10_485_760

    ocr_enabled: bool = True
    ocr_dpi: int = 200
    ocr_language: str = "eng"
    tesseract_cmd: str | None = None

    redis_url: str = "redis://localhost:6379/0"
    idempotency_ttl_seconds: int = 86_400

    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Security
    webhook_signing_secret: str | None = None
    require_webhook_signature: bool = False
    log_secrets_redaction: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
