import random

import pytest

from chatraid.analyzer import ChatAnalyzer
from chatraid.engine import GameEngine
from chatraid.models import EventProposal
from chatraid.safety import SafetyFilter
from chatraid.storage import Storage


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "t.db"))


@pytest.fixture
def engine(clock, storage):
    e = GameEngine(storage, SafetyFilter(), clock, random.Random(1))
    e.start("fantasy", 3)
    e.chat_replies.clear()
    return e


@pytest.fixture
def analyzer(clock):
    return ChatAnalyzer(clock=clock)


def make_proposal(**kw):
    base = dict(type="enemy", title="Test Orc", description="An orc appears", duration_seconds=60,
                difficulty=3, enemy_hp=100, success_condition="Defeat it", reward_points=50)
    base.update(kw)
    return EventProposal(**base)


def join(engine, clock, uid="u1", name="Sam", cls="warrior"):
    engine.handle_command(uid, name, f"!join {cls}")
    clock.advance(3)


@pytest.fixture(autouse=True)
def _isolated_keyfile(tmp_path, monkeypatch):
    """Token-vault tests must never write .chatraid.key into the working directory."""
    monkeypatch.setenv("CHATRAID_KEYFILE", str(tmp_path / "test.key"))
