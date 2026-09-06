"""إعدادات التطبيق — تُقرأ من متغيّرات البيئة أو من ملف .env"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(..., description="توكن البوت من BotFather")

    db_path: Path = Path("data/bot.db")
    pages_dir: Path = Path("assets/pages")

    default_timezone: str = "Asia/Damascus"
    log_level: str = "INFO"

    # وضع التشغيل: إن كان webhook_url مضبوطًا يعمل بالـ webhook، وإلا long polling
    webhook_url: str = ""
    secret_token: str = ""
    port: int = 8443
    url_path: str = ""

    @field_validator("db_path", "pages_dir")
    @classmethod
    def _absolutise(cls, v: Path) -> Path:
        return v if v.is_absolute() else PROJECT_ROOT / v

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url.strip())

    @property
    def effective_url_path(self) -> str:
        """مسار الـ endpoint — يُشتقّ من التوكن إن لم يُحدَّد."""
        return (self.url_path or self.bot_token).strip("/")

    @property
    def full_webhook_url(self) -> str:
        return f"{self.webhook_url.rstrip('/')}/{self.effective_url_path}"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
