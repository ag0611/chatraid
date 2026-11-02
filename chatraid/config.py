"""Environment-based configuration. Secrets only come from env vars (never the repo)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    twitch_client_id: str = field(default_factory=lambda: _env("TWITCH_CLIENT_ID"))
    twitch_client_secret: str = field(default_factory=lambda: _env("TWITCH_CLIENT_SECRET"))
    twitch_redirect_uri: str = field(
        default_factory=lambda: _env("TWITCH_REDIRECT_URI", "http://localhost:8000/auth/twitch/callback"))
    twitch_channel: str = field(default_factory=lambda: _env("TWITCH_CHANNEL").lower().lstrip("#"))
    twitch_bot_token: str = field(default_factory=lambda: _env("TWITCH_BOT_TOKEN"))  # oauth:xxxx for sending chat
    twitch_bot_nick: str = field(default_factory=lambda: _env("TWITCH_BOT_NICK"))
    eventsub_secret: str = field(default_factory=lambda: _env("TWITCH_EVENTSUB_SECRET", secrets.token_hex(16)))
    secret_key: str = field(default_factory=lambda: _env("CHATRAID_SECRET_KEY"))  # Fernet key
    admin_token: str = field(default_factory=lambda: _env("CHATRAID_ADMIN_TOKEN") or secrets.token_urlsafe(16))
    db_path: str = field(default_factory=lambda: _env("CHATRAID_DB", "chatraid.db"))
    director_interval: float = field(default_factory=lambda: float(_env("CHATRAID_DIRECTOR_INTERVAL", "15")))
    demo: bool = field(default_factory=lambda: _env("CHATRAID_DEMO") == "1")
    theme: str = field(default_factory=lambda: _env("CHATRAID_THEME", "fantasy"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))

    @property
    def twitch_configured(self) -> bool:
        return bool(self.twitch_channel)
