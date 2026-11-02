"""Burst / load behaviour: 100 msg/s burst with 500 simulated chatters stays fast and consistent."""
import time

from chatraid.config import Settings
from chatraid.models import ChatMessage
from chatraid.runtime import Runtime
from tests.conftest import make_proposal


def test_burst_500_chatters_latency(tmp_path):
    rt = Runtime(Settings(db_path=str(tmp_path / "l.db"), admin_token="x", openai_api_key=""))
    rt.engine.start()
    for i in range(500):
        rt.engine.handle_command(f"u{i}", f"user{i}", "!join")  # setup bypasses the channel throttle
    rt.engine.start_event(make_proposal(enemy_hp=5000, difficulty=2))
    t0 = time.perf_counter()
    for sec in range(3):  # 3 seconds x 100 msgs/s of mixed commands + chatter
        for i in range(100):
            uid = f"u{(sec * 100 + i) % 500}"
            text = "!attack" if i % 2 else "pog this is great"
            rt.on_chat(ChatMessage(user_id=uid, display_name=uid, text=text))
        rt.engine.cooldowns.clear()
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"processing 300 msgs took {elapsed:.2f}s"
    assert rt.engine.event.hp < 5000
    assert rt.analyzer.pulse().msg_rate > 0
    assert rt.metrics["throttled"] <= 0 or rt.metrics["commands"] > 0
    snap_t = time.perf_counter()
    rt.engine.snapshot()
    assert time.perf_counter() - snap_t < 0.05


def test_channel_throttle_drops_excess_commands(tmp_path, monkeypatch):
    rt = Runtime(Settings(db_path=str(tmp_path / "t.db"), admin_token="x", openai_api_key=""))
    rt.engine.start()
    monkeypatch.setattr(time, "time", lambda: 5000.0)
    for i in range(250):
        rt.on_chat(ChatMessage(user_id=f"u{i}", display_name="n", text="!join"))
    assert rt.metrics["throttled"] == 150 and rt.metrics["commands"] == 100
