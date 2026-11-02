"""Shared schemas for ChatRaid."""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    user_id: str
    display_name: str
    text: str
    ts: float = Field(default_factory=time.time)


class EventProposal(BaseModel):
    """Structured output of the AI Director (spec section 7.7)."""

    type: Literal["enemy", "quest", "hazard", "twist", "community_goal"]
    title: str = Field(max_length=60)
    description: str = Field(max_length=200)
    duration_seconds: int = Field(ge=15, le=180)
    difficulty: int = Field(ge=1, le=10)
    enemy_hp: int | None = Field(default=None, ge=50, le=5000)
    success_condition: str
    reward_points: int = Field(ge=10, le=500)
    streamer_effect: str | None = None  # needs streamer approval


class ChatPulse(BaseModel):
    msg_rate: float = 0.0  # messages per second over the window
    active_users: int = 0
    sentiment: float = 0.0  # -1..1
    hype: float = 0.0  # 0..1
    top_keywords: list[str] = []
    quiet: bool = True
    spam: bool = False


class Player(BaseModel):
    user_id: str
    display_name: str
    char_class: Literal["warrior", "healer", "trickster"] = "warrior"
    points: int = 0
    level: int = 1
    actions: int = 0
    damage: int = 0
    healing: int = 0


class ActiveEvent(BaseModel):
    id: int
    proposal: EventProposal
    source: Literal["director", "template", "twitch"] = "director"
    started_at: float
    ends_at: float
    hp: int = 0
    max_hp: int = 0
    progress: int = 0  # for quests / goals
    target: int = 0
    votes: dict[str, str] = {}  # user_id -> option (for twists)
    status: Literal["active", "won", "lost", "skipped"] = "active"
    awaiting_approval: bool = False


class Ability(BaseModel):
    key: str
    name: str
    description: str
    cost: int
    unlocked: bool = False
