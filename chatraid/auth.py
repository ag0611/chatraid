"""Twitch OAuth (authorization-code flow) + encrypted token vault (FR1)."""
from __future__ import annotations

import os
import secrets
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet

from .config import Settings
from .storage import Storage

SCOPES = [
    "chat:read", "chat:edit", "channel:read:redemptions", "channel:read:subscriptions",
    "moderator:read:followers",
]
AUTHORIZE = "https://id.twitch.tv/oauth2/authorize"
TOKEN = "https://id.twitch.tv/oauth2/token"
HELIX_USERS = "https://api.twitch.tv/helix/users"


def _fernet(settings: Settings) -> Fernet:
    key = settings.secret_key
    if not key:  # dev convenience: persist a generated key outside the repo tree's tracked files
        keyfile = Path(os.getenv("CHATRAID_KEYFILE", ".chatraid.key"))
        if keyfile.exists():
            key = keyfile.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            keyfile.write_text(key)
            keyfile.chmod(0o600)
    return Fernet(key.encode())


class TokenVault:
    """Stores OAuth tokens encrypted at rest in the world table."""

    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.f = _fernet(settings)
        self.storage = storage

    def save(self, streamer: dict[str, Any]) -> None:
        enc = dict(streamer)
        for k in ("access_token", "refresh_token"):
            if enc.get(k):
                enc[k] = self.f.encrypt(enc[k].encode()).decode()
        self.storage.set_world("streamer", enc)

    def load(self) -> dict[str, Any] | None:
        raw = self.storage.get_world("streamer")
        if not raw:
            return None
        for k in ("access_token", "refresh_token"):
            if raw.get(k):
                raw[k] = self.f.decrypt(raw[k].encode()).decode()
        return raw


class TwitchOAuth:
    def __init__(self, settings: Settings, vault: TokenVault) -> None:
        self.s, self.vault = settings, vault
        self._states: set[str] = set()

    def login_url(self) -> str:
        state = secrets.token_urlsafe(16)
        self._states.add(state)
        q = urllib.parse.urlencode({
            "client_id": self.s.twitch_client_id, "redirect_uri": self.s.twitch_redirect_uri,
            "response_type": "code", "scope": " ".join(SCOPES), "state": state})
        return f"{AUTHORIZE}?{q}"

    async def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        if state not in self._states:
            raise PermissionError("invalid OAuth state")
        self._states.discard(state)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(TOKEN, data={
                "client_id": self.s.twitch_client_id, "client_secret": self.s.twitch_client_secret,
                "code": code, "grant_type": "authorization_code", "redirect_uri": self.s.twitch_redirect_uri})
            r.raise_for_status()
            tok = r.json()
            u = await c.get(HELIX_USERS, headers={
                "Client-Id": self.s.twitch_client_id, "Authorization": f"Bearer {tok['access_token']}"})
            u.raise_for_status()
            user = u.json()["data"][0]
        streamer = {
            "twitch_id": user["id"], "display_name": user["display_name"], "login": user["login"],
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
        }
        self.vault.save(streamer)
        return {k: v for k, v in streamer.items() if "token" not in k}
