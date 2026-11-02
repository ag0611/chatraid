import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from chatraid.app import create_app
from chatraid.config import Settings
from chatraid.models import ChatMessage
from chatraid.runtime import Runtime

ADMIN = {"X-Admin-Token": "secret-admin"}


@pytest.fixture
def rt(tmp_path):
    s = Settings(db_path=str(tmp_path / "app.db"), admin_token="secret-admin", openai_api_key="",
                 secret_key="", eventsub_secret="es-secret", director_interval=0.1)
    return Runtime(s)


@pytest.fixture
def client(rt):
    with TestClient(create_app(runtime=rt)) as c:
        yield c


def test_admin_required(client):
    assert client.post("/sessions/start").status_code == 401
    assert client.get("/settings").status_code == 401
    assert client.post("/sessions/start", headers=ADMIN).status_code == 200


def test_public_endpoints(client):
    assert client.get("/healthz").json()["ok"]
    assert client.get("/state").json()["status"] == "idle"
    assert "session" in client.get("/leaderboard").json()
    assert client.get("/overlay").status_code == 200 and client.get("/dashboard").status_code == 200


def test_full_session_flow_and_skip(client, rt):
    assert client.post("/sessions/start", json={"theme": "scifi", "difficulty": 4}, headers=ADMIN).json()["state"]["theme"] == "scifi"
    rt.on_chat(ChatMessage(user_id="1", display_name="Sam", text="!join healer"))
    assert "1" in rt.engine.players
    client.post("/director/trigger", headers=ADMIN)
    assert rt.planned is not None
    assert client.post("/director/approve", headers=ADMIN).json()["approved"]
    for _ in range(40):
        time.sleep(0.1)
        if rt.engine.event:
            break
    ev = rt.engine.event
    assert ev and ev.status == "active"
    assert client.post(f"/events/{ev.id}/skip", headers=ADMIN).status_code == 200
    assert client.post("/events/999/skip", headers=ADMIN).status_code == 404
    r = client.post("/sessions/1/end", headers=ADMIN).json()
    assert r["status"] == "ended" and "recap" in r


def test_settings_blocklist_and_validation(client, rt):
    r = client.put("/settings", json={"difficulty": 7, "blocklist": ["kiwi"], "theme": "spooky"}, headers=ADMIN).json()
    assert r["difficulty"] == 7 and "kiwi" in r["blocklist"] and rt.engine.theme == "spooky"
    assert client.put("/settings", json={"difficulty": 99}, headers=ADMIN).status_code == 422
    assert client.put("/settings", json={"theme": "pirate"}, headers=ADMIN).status_code == 422


def test_overlay_websocket_token_and_revocation(client, rt):
    with pytest.raises(Exception), client.websocket_connect("/ws/overlay/wrong"):
        pass
    old = rt.manager.overlay_token
    with client.websocket_connect(f"/ws/overlay/{old}") as ws:
        assert ws.receive_json()["type"] == "state_update"
    new = client.post("/overlay/rotate", headers=ADMIN).json()["overlay_url"].split("=")[1]
    assert new != old
    with pytest.raises(Exception), client.websocket_connect(f"/ws/overlay/{old}"):
        pass


def test_dashboard_ws_requires_admin_and_gets_pulse(client):
    with pytest.raises(Exception), client.websocket_connect("/ws/dashboard?token=nope"):
        pass
    with client.websocket_connect("/ws/dashboard?token=secret-admin") as ws:
        assert ws.receive_json()["type"] == "state_update"
        seen = set()
        for _ in range(8):
            seen.add(ws.receive_json()["type"])
            if "chat_pulse" in seen:
                break
        assert "chat_pulse" in seen


def test_kill_switch_endpoint(client, rt):
    client.post("/sessions/start", headers=ADMIN)
    assert client.post("/kill", headers=ADMIN).json()["kill_switch"]
    assert rt.engine.kill_switch and rt.planned is None


def test_feedback_validation_and_recap(client):
    client.post("/sessions/start", headers=ADMIN)
    assert client.post("/feedback", json={"rating": 5, "comment": "fun"}).status_code == 200
    assert client.post("/feedback", json={"rating": 9}).status_code == 422
    assert client.get("/recap", headers=ADMIN).json()["avg_rating"] == 5


def test_eventsub_webhook_signature_and_challenge(client, rt):
    def sign(mid, ts, body):
        return "sha256=" + hmac.new(b"es-secret", (mid + ts).encode() + body, hashlib.sha256).hexdigest()

    body = b'{"challenge":"abc123"}'
    h = {"Twitch-Eventsub-Message-Id": "m1", "Twitch-Eventsub-Message-Timestamp": "t",
         "Twitch-Eventsub-Message-Type": "webhook_callback_verification"}
    assert client.post("/twitch/eventsub", content=body, headers={**h, "Twitch-Eventsub-Message-Signature": "sha256=bad"}).status_code == 403
    r = client.post("/twitch/eventsub", content=body, headers={**h, "Twitch-Eventsub-Message-Signature": sign("m1", "t", body)})
    assert r.text == "abc123"
    n = b'{"subscription":{"type":"channel.subscribe"},"event":{"user_name":"Fan"}}'
    h["Twitch-Eventsub-Message-Type"] = "notification"
    r = client.post("/twitch/eventsub", content=n, headers={**h, "Twitch-Eventsub-Message-Signature": sign("m1", "t", n)})
    assert r.status_code == 204 and rt.engine.community_power == 25


def test_simulator_endpoints(client, rt):
    client.post("/sessions/start", headers=ADMIN)
    assert client.post("/simulate/start", json={"scenario": "hype", "viewers": 10}, headers=ADMIN).status_code == 200
    assert client.post("/simulate/start", json={"scenario": "nope"}, headers=ADMIN).status_code == 400
    time.sleep(1.5)
    assert len(rt.engine.players) > 0
    client.post("/simulate/stop", headers=ADMIN)


def test_oauth_login_requires_config_and_state_check(client, rt):
    assert client.get("/auth/twitch/login").status_code == 503
    rt.s.twitch_client_id = "cid"
    r = client.get("/auth/twitch/login", follow_redirects=False)
    assert r.status_code in (302, 307) and "id.twitch.tv" in r.headers["location"]
    assert client.get("/auth/twitch/callback?code=x&state=forged").status_code == 400


def test_token_vault_encrypts_at_rest(rt):
    rt.vault.save({"twitch_id": "1", "access_token": "plain-secret", "refresh_token": "r"})
    raw = rt.storage.get_world("streamer")
    assert "plain-secret" not in str(raw)
    assert rt.vault.load()["access_token"] == "plain-secret"
