from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Project Expense Manager"
    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "sqlite:///./storage/expense_manager.db"
    storage_root: str = "storage"
    cors_origins: str = "http://localhost:5173"
    default_currency: str = "LKR"

    telegram_bot_token: str | None = None
    telegram_bot_enabled: bool = True
    telegram_admin_id: int | None = None
    max_receipt_upload_size_mb: int = Field(default=10, gt=0)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_receipt_upload_size_bytes(self) -> int:
        return self.max_receipt_upload_size_mb * 1024 * 1024


settings = Settings()
