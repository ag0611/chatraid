"""FastAPI application: REST, WebSockets, static overlay + dashboard."""
from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from .config import Settings
from .gateway import normalize_eventsub
from .models import ChatMessage
from .runtime import Runtime
from .simulator import SCENARIOS

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("chatraid")


class StartBody(BaseModel):
    theme: str | None = Field(default=None, pattern="^(fantasy|scifi|spooky)$")
    difficulty: int | None = Field(default=None, ge=1, le=10)


class SettingsBody(BaseModel):
    theme: str | None = Field(default=None, pattern="^(fantasy|scifi|spooky)$")
    difficulty: int | None = Field(default=None, ge=1, le=10)
    blocklist: list[str] | None = None
    auto_approve: bool | None = None


class FeedbackBody(BaseModel):
    role: str = Field(default="viewer", pattern="^(streamer|viewer)$")
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class SimBody(BaseModel):
    scenario: str = "mixed"
    viewers: int = Field(default=20, ge=1, le=500)


class ChatBody(BaseModel):
    user_id: str = Field(max_length=40)
    display_name: str = Field(max_length=40)
    text: str = Field(max_length=500)


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    rt = runtime or Runtime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await rt.start()
        print(f"\n  ChatRaid dashboard : http://localhost:8000/dashboard#{rt.s.admin_token}")
        print(f"  OBS overlay URL    : http://localhost:8000/overlay?token={rt.manager.overlay_token}\n")
        yield
        await rt.stop()

    app = FastAPI(title="ChatRaid", version="1.0.0", lifespan=lifespan)
    app.state.rt = rt

    async def admin(x_admin_token: str | None = Header(default=None), token: str | None = Query(default=None)):
        given = x_admin_token or token or ""
        if not hmac.compare_digest(given, rt.s.admin_token):
            raise HTTPException(401, "invalid admin token")

    # ------------------------------------------------------------------ pages
    @app.get("/", include_in_schema=False)
    async def index():
        return RedirectResponse("/dashboard")

    @app.get("/overlay", include_in_schema=False)
    async def overlay():
        return FileResponse(STATIC / "overlay.html")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        return FileResponse(STATIC / "dashboard.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return PlainTextResponse("", status_code=204)

    @app.get("/healthz")
    async def health():
        return {"ok": True, "status": rt.engine.status}

    # ------------------------------------------------------------------ auth
    @app.get("/auth/twitch/login")
    async def twitch_login():
        if not rt.s.twitch_client_id:
            raise HTTPException(503, "TWITCH_CLIENT_ID is not configured")
        return RedirectResponse(rt.oauth.login_url())

    @app.get("/auth/twitch/callback")
    async def twitch_callback(code: str = "", state: str = "", error: str = ""):
        if error or not code:
            raise HTTPException(400, f"Twitch denied login: {error or 'no code'}")
        try:
            info = await rt.oauth.handle_callback(code, state)
        except PermissionError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"connected": info, "next": "Restart ChatRaid (or set TWITCH_CHANNEL) to join chat"}

    # ------------------------------------------------------------------ sessions
    @app.post("/sessions/start", dependencies=[Depends(admin)])
    async def session_start(body: StartBody | None = None):
        body = body or StartBody()
        rt.engine.start(body.theme, body.difficulty)
        await rt.push_state()
        return {"session_id": rt.engine.session_id, "state": rt.engine.snapshot()}

    @app.post("/sessions/{sid}/pause", dependencies=[Depends(admin)])
    async def session_pause(sid: int):
        rt.engine.pause()
        await rt.push_state()
        return {"status": rt.engine.status}

    @app.post("/sessions/{sid}/resume", dependencies=[Depends(admin)])
    async def session_resume(sid: int):
        rt.engine.resume()
        await rt.push_state()
        return {"status": rt.engine.status}

    @app.post("/sessions/{sid}/end", dependencies=[Depends(admin)])
    async def session_end(sid: int):
        sid_real = rt.engine.session_id
        rt.engine.end()
        rt.planned = None
        await rt.push_state()
        return {"status": rt.engine.status, "recap": rt.storage.recap(sid_real)}

    @app.post("/kill", dependencies=[Depends(admin)])
    async def kill():
        rt.engine.kill()
        rt.planned = None
        await rt.push_state()
        return {"kill_switch": True}

    @app.post("/events/{event_id}/skip", dependencies=[Depends(admin)])
    async def skip_event(event_id: int):
        ev = rt.engine.event
        if not ev or ev.id != event_id or ev.status != "active":
            raise HTTPException(404, "no such active event")
        rt.engine.skip_event()
        await rt.push_state()
        return {"skipped": event_id}

    # ------------------------------------------------------------------ director
    @app.post("/director/trigger", dependencies=[Depends(admin)])
    async def director_trigger():
        await rt.trigger_event("manual")
        return {"plan": rt.plan_view()}

    @app.post("/director/approve", dependencies=[Depends(admin)])
    async def director_approve():
        return {"approved": rt.approve_plan()}

    @app.post("/director/skip", dependencies=[Depends(admin)])
    async def director_skip():
        return {"skipped": rt.reject_plan()}

    @app.post("/effect/{decision}", dependencies=[Depends(admin)])
    async def effect(decision: str):
        if decision not in ("approve", "deny"):
            raise HTTPException(400, "approve or deny")
        rt.engine.approve_effect(decision == "approve")
        await rt.push_state()
        return {"decision": decision}

    # ------------------------------------------------------------------ state
    @app.get("/state")
    async def state():
        return rt.engine.snapshot()

    @app.get("/leaderboard")
    async def leaderboard(n: int = Query(default=10, ge=1, le=50)):
        return {"session": rt.engine.top_players(n), "all_time": rt.storage.leaderboard(n)}

    @app.put("/settings", dependencies=[Depends(admin)])
    async def put_settings(body: SettingsBody):
        if body.theme:
            rt.engine.theme = body.theme
        if body.difficulty:
            rt.engine.set_difficulty(body.difficulty)
        if body.blocklist is not None:
            rt.safety.set_blocklist(body.blocklist)
        if body.auto_approve is not None:
            rt.auto_approve = body.auto_approve
        return {"theme": rt.engine.theme, "difficulty": rt.engine.difficulty,
                "auto_approve": rt.auto_approve, "blocklist": sorted(rt.safety.blocklist)}

    @app.get("/settings", dependencies=[Depends(admin)])
    async def get_settings():
        return {"theme": rt.engine.theme, "difficulty": rt.engine.difficulty, "auto_approve": rt.auto_approve,
                "blocklist": sorted(rt.safety.blocklist),
                "overlay_url": f"/overlay?token={rt.manager.overlay_token}"}

    @app.post("/overlay/rotate", dependencies=[Depends(admin)])
    async def rotate():
        return {"overlay_url": f"/overlay?token={rt.manager.rotate_token()}"}

    @app.post("/players/{uid}/ban", dependencies=[Depends(admin)])
    async def ban(uid: str):
        rt.engine.banned.add(uid)
        return {"banned": uid}

    @app.post("/feedback")
    async def feedback(body: FeedbackBody):
        rt.storage.add_feedback(rt.engine.session_id, body.role, body.rating, body.comment)
        return {"ok": True}

    @app.get("/recap", dependencies=[Depends(admin)])
    async def recap():
        return rt.storage.recap(rt.engine.session_id) | {"top": rt.engine.top_players(5)}

    # ------------------------------------------------------------------ simulator
    @app.post("/simulate/start", dependencies=[Depends(admin)])
    async def sim_start(body: SimBody):
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, f"scenario must be one of {SCENARIOS}")
        rt.start_simulator(body.scenario, body.viewers)
        return {"simulating": body.scenario}

    @app.post("/simulate/stop", dependencies=[Depends(admin)])
    async def sim_stop():
        rt.stop_simulator()
        return {"simulating": None}

    @app.post("/simulate/chat", dependencies=[Depends(admin)])
    async def sim_chat(body: ChatBody):
        rt.on_chat(ChatMessage(**body.model_dump()))
        return {"ok": True}

    # ------------------------------------------------------------------ Twitch EventSub webhook transport
    @app.post("/twitch/eventsub")
    async def eventsub_webhook(request: Request):
        body = await request.body()
        h = request.headers
        msg = (h.get("twitch-eventsub-message-id", "") + h.get("twitch-eventsub-message-timestamp", "")).encode() + body
        expected = "sha256=" + hmac.new(rt.s.eventsub_secret.encode(), msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, h.get("twitch-eventsub-message-signature", "")):
            raise HTTPException(403, "bad signature")
        data: dict[str, Any] = await request.json()
        mtype = h.get("twitch-eventsub-message-type")
        if mtype == "webhook_callback_verification":
            return PlainTextResponse(data["challenge"])
        if mtype == "notification":
            norm = normalize_eventsub(data["subscription"]["type"], data["event"])
            if norm:
                await rt.on_twitch_event(norm)
        return PlainTextResponse("", status_code=204)

    # ------------------------------------------------------------------ websockets
    @app.websocket("/ws/overlay/{token}")
    async def ws_overlay(ws: WebSocket, token: str):
        if not hmac.compare_digest(token, rt.manager.overlay_token):
            await ws.close(code=4403)
            return
        await ws.accept()
        rt.manager.overlays.add(ws)
        try:
            await ws.send_json({"type": "state_update", "state": rt.engine.snapshot()})
            while True:
                await ws.receive_text()  # keepalive; overlay is receive-only
        except WebSocketDisconnect:
            pass
        finally:
            rt.manager.overlays.discard(ws)

    @app.websocket("/ws/dashboard")
    async def ws_dashboard(ws: WebSocket, token: str = ""):
        if not hmac.compare_digest(token, rt.s.admin_token):
            await ws.close(code=4401)
            return
        await ws.accept()
        rt.manager.dashboards.add(ws)
        try:
            await ws.send_json({"type": "state_update", "state": rt.engine.snapshot()})
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            rt.manager.dashboards.discard(ws)

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
