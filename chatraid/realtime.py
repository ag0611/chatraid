"""WebSocket fan-out to overlay and dashboard clients."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("chatraid.realtime")


class ConnectionManager:
    def __init__(self) -> None:
        self.overlays: set[WebSocket] = set()
        self.dashboards: set[WebSocket] = set()
        self.overlay_token = secrets.token_urlsafe(12)  # random, revocable

    def rotate_token(self) -> str:
        """Revoke the overlay URL: disconnect existing overlays and issue a new token."""
        self.overlay_token = secrets.token_urlsafe(12)
        old = list(self.overlays)
        self.overlays.clear()
        for ws in old:
            asyncio.create_task(self._close(ws))
        return self.overlay_token

    @staticmethod
    async def _close(ws: WebSocket) -> None:
        try:
            await ws.close(code=4403)
        except Exception:  # already closed
            pass

    async def _send_all(self, group: set[WebSocket], payload: dict[str, Any]) -> None:
        if not group:
            return
        data = json.dumps(payload)
        dead = []
        for ws in list(group):
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            group.discard(ws)

    async def to_overlays(self, payload: dict[str, Any]) -> None:
        await self._send_all(self.overlays, payload)

    async def to_dashboards(self, payload: dict[str, Any]) -> None:
        await self._send_all(self.dashboards, payload)

    async def to_all(self, payload: dict[str, Any]) -> None:
        await asyncio.gather(self.to_overlays(payload), self.to_dashboards(payload))
