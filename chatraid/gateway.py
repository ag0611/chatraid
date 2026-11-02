"""Twitch Gateway: chat (IRC over TLS) + EventSub (WebSocket transport).

Both normalise Twitch traffic into internal events and hand them to callbacks.
NOTE: these talk to the live Twitch service and need real credentials; the rest of
the system is exercised offline through the chat simulator and unit tests.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import websockets

from .models import ChatMessage

log = logging.getLogger("chatraid.gateway")

IRC_HOST, IRC_PORT = "irc.chat.twitch.tv", 6697
EVENTSUB_WS = "wss://eventsub.wss.twitch.tv/ws"
HELIX = "https://api.twitch.tv/helix"


def parse_irc_line(line: str) -> dict[str, Any] | None:
    """Parse one IRC line (with IRCv3 tags) into {tags, command, channel, user, text}."""
    tags: dict[str, str] = {}
    rest = line
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for kv in raw_tags.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = v
    prefix = ""
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")
    cmd, _, params = rest.partition(" ")
    channel, _, trailing = params.partition(" :")
    return {
        "tags": tags, "prefix": prefix, "command": cmd, "channel": channel.strip(),
        "user": prefix.split("!")[0], "text": trailing,
    }


def message_from_irc(parsed: dict[str, Any]) -> ChatMessage | None:
    if parsed["command"] != "PRIVMSG":
        return None
    tags = parsed["tags"]
    text = parsed["text"]
    if text.startswith("\x01ACTION "):  # /me
        text = text[8:].rstrip("\x01")
    return ChatMessage(
        user_id=tags.get("user-id") or parsed["user"],
        display_name=tags.get("display-name") or parsed["user"],
        text=text,
    )


class TwitchChat:
    """Reads chat anonymously (justinfan) and can send messages when a bot token is provided."""

    def __init__(self, channel: str, on_message: Callable[[ChatMessage], Any],
                 nick: str = "", token: str = "") -> None:
        self.channel = channel.lower().lstrip("#")
        self.on_message = on_message
        self.nick = nick.lower() or "justinfan48213"
        self.token = token if token.startswith("oauth:") else (f"oauth:{token}" if token else "SCHMOOPIIE")
        self.can_send = bool(nick and token)
        self._writer: asyncio.StreamWriter | None = None
        self._last_send = 0.0
        self._stop = False
        self.connected = False

    async def _send_raw(self, line: str) -> None:
        if self._writer:
            self._writer.write((line + "\r\n").encode())
            await self._writer.drain()

    async def say(self, text: str) -> None:
        if not (self.can_send and self._writer):
            return
        wait = 1.6 - (time.monotonic() - self._last_send)  # stay well under chat rate limits
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = time.monotonic()
        await self._send_raw(f"PRIVMSG #{self.channel} :{text[:450]}")

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                ctx = ssl.create_default_context()
                reader, writer = await asyncio.open_connection(IRC_HOST, IRC_PORT, ssl=ctx)
                self._writer = writer
                await self._send_raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await self._send_raw(f"PASS {self.token}")
                await self._send_raw(f"NICK {self.nick}")
                await self._send_raw(f"JOIN #{self.channel}")
                self.connected = True
                backoff = 1.0
                log.info("Joined #%s", self.channel)
                while not self._stop:
                    raw = await asyncio.wait_for(reader.readline(), timeout=360)
                    if not raw:
                        break
                    for line in raw.decode(errors="ignore").split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await self._send_raw("PONG :tmi.twitch.tv")
                            continue
                        parsed = parse_irc_line(line)
                        msg = message_from_irc(parsed) if parsed else None
                        if msg:
                            res = self.on_message(msg)
                            if asyncio.iscoroutine(res):
                                await res
            except Exception as exc:
                log.warning("IRC connection lost: %s (retry in %.0fs)", exc, backoff)
            finally:
                self.connected = False
                if self._writer:
                    self._writer.close()
                    self._writer = None
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)


def normalize_eventsub(sub_type: str, ev: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Twitch EventSub notification to an internal {kind, user, ...} event."""
    if sub_type == "channel.follow":
        return {"kind": "follow", "user": ev.get("user_name", "someone")}
    if sub_type in ("channel.subscribe", "channel.subscription.message"):
        return {"kind": "sub", "user": ev.get("user_name", "someone")}
    if sub_type == "channel.raid":
        return {"kind": "raid", "user": ev.get("from_broadcaster_user_name", "someone"),
                "viewers": int(ev.get("viewers", 0))}
    if sub_type == "channel.channel_points_custom_reward_redemption.add":
        return {"kind": "redeem", "user": ev.get("user_name", "someone"),
                "reward": (ev.get("reward") or {}).get("title", "")}
    return None


class EventSubClient:
    """EventSub over the WebSocket transport (no public URL needed)."""

    def __init__(self, client_id: str, access_token: str, broadcaster_id: str,
                 on_event: Callable[[dict[str, Any]], Awaitable[None] | None]) -> None:
        self.client_id, self.token, self.bid = client_id, access_token, broadcaster_id
        self.on_event = on_event
        self._stop = False
        self.connected = False

    def stop(self) -> None:
        self._stop = True

    async def _subscribe(self, session_id: str) -> None:
        headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {self.token}"}
        subs = [
            ("channel.follow", "2", {"broadcaster_user_id": self.bid, "moderator_user_id": self.bid}),
            ("channel.subscribe", "1", {"broadcaster_user_id": self.bid}),
            ("channel.raid", "1", {"to_broadcaster_user_id": self.bid}),
            ("channel.channel_points_custom_reward_redemption.add", "1", {"broadcaster_user_id": self.bid}),
        ]
        async with httpx.AsyncClient(timeout=10, headers=headers) as c:
            for typ, ver, cond in subs:
                r = await c.post(f"{HELIX}/eventsub/subscriptions", json={
                    "type": typ, "version": ver, "condition": cond,
                    "transport": {"method": "websocket", "session_id": session_id}})
                if r.status_code >= 300:
                    log.warning("EventSub %s failed: %s %s", typ, r.status_code, r.text[:120])

    async def run(self) -> None:
        url, backoff = EVENTSUB_WS, 1.0
        while not self._stop:
            try:
                async with websockets.connect(url) as ws:
                    self.connected = True
                    backoff = 1.0
                    async for raw in ws:
                        msg = json.loads(raw)
                        mtype = msg["metadata"]["message_type"]
                        if mtype == "session_welcome":
                            await self._subscribe(msg["payload"]["session"]["id"])
                        elif mtype == "session_reconnect":
                            url = msg["payload"]["session"]["reconnect_url"]
                            break
                        elif mtype == "notification":
                            norm = normalize_eventsub(msg["metadata"]["subscription_type"],
                                                      msg["payload"]["event"])
                            if norm:
                                res = self.on_event(norm)
                                if asyncio.iscoroutine(res):
                                    await res
                    else:
                        url = EVENTSUB_WS
            except Exception as exc:
                log.warning("EventSub lost: %s (retry in %.0fs)", exc, backoff)
                url = EVENTSUB_WS
            finally:
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)
