"""Конфигурация приложения.

Все значения читаются из переменных окружения / .env (pydantic-settings).
Секреты никогда не хардкодятся в коде.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Общее ---
    app_name: str = "Drone Alert Radar"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://radar:radar@localhost:5432/radar"
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    auto_create_tables: bool = True

    # --- Окна жизненного цикла алертов ---
    active_window_minutes: int = 240
    all_clear_window_minutes: int = 90
    dedup_window_minutes: int = 120
    dedup_fuzzy_window_minutes: int = 20
    dedup_similarity_threshold: float = 0.55

    # --- Telegram ---
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session: str = "data/radar"
    telegram_session_string: str = ""
    telegram_channels: str = ""
    telegram_backfill_limit: int = 20
    # Прокси для выхода в Telegram. Поддерживаются схемы:
    #   socks5://[user:pass@]host:port
    #   http://[user:pass@]host:port
    #   mtproxy://secret@host:port  (секрет — 32 hex-символа)
    telegram_proxy: str = ""

    # --- RSS ---
    rss_feeds: str = ""
    rss_poll_seconds: int = 90

    # --- Уведомления ---
    notify_enabled: bool = True
    notify_scheduler_enabled: bool = True
    notify_poll_seconds: int = 20
    notify_window_minutes: int = 30
    notify_all_clear: bool = True
    notify_regions: str = ""
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_bot_chat_id: str = ""

    # --- Приём из внешних API ---
    ingest_token: str = ""

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def _empty_api_id(cls, value: object) -> object:
        """Пустая строка в .env не должна ронять валидацию."""
        if value in ("", None, "None"):
            return None
        return value

    @staticmethod
    def _split(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return self._split(self.cors_origins)

    @property
    def telegram_channel_list(self) -> list[str]:
        return self._split(self.telegram_channels)

    @property
    def rss_feed_list(self) -> list[str]:
        return self._split(self.rss_feeds)

    @property
    def notify_region_list(self) -> list[str]:
        return self._split(self.notify_regions)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def telegram_proxy_config(self) -> dict[str, object] | None:
        """Разбирает TELEGRAM_PROXY в параметры для Telethon.

        Возвращает словарь с ключами:
          kind   — «socks5», «http» или «mtproxy»;
          host   — адрес прокси;
          port   — порт;
          user   — логин (для socks5/http);
          password — пароль (для socks5/http);
          secret — секрет MTProto-прокси.
        Пустая строка или некорректный URL → None (подключение напрямую).
        """
        from urllib.parse import unquote, urlsplit

        raw = (self.telegram_proxy or "").strip()
        if not raw:
            return None

        parts = urlsplit(raw)
        kind = (parts.scheme or "").lower()
        if kind in ("socks5", "socks5h", "socks"):
            kind = "socks5"
        elif kind in ("http", "https"):
            kind = "http"
        elif kind in ("mtproxy", "mtproto"):
            kind = "mtproxy"
        else:
            return None

        if not parts.hostname or not parts.port:
            return None

        config: dict[str, object] = {
            "kind": kind,
            "host": parts.hostname,
            "port": parts.port,
        }
        if kind == "mtproxy":
            secret = unquote(parts.username or "") or parts.path.lstrip("/")
            if not secret:
                return None
            config["secret"] = secret
        else:
            config["user"] = unquote(parts.username) if parts.username else None
            config["password"] = unquote(parts.password) if parts.password else None
        return config


    @property
    def notify_configured(self) -> bool:
        return bool(
            (self.discord_webhook_url or (self.telegram_bot_token and self.telegram_bot_chat_id))
            and self.notify_enabled
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
