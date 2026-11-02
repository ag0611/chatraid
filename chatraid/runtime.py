"""Runtime: wires gateway -> event bus -> engine/analyzer/director -> realtime."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .analyzer import ChatAnalyzer
from .auth import TokenVault, TwitchOAuth
from .config import Settings
from .director import Director, DirectorPlan, OpenAIClient
from .engine import GameEngine
from .gateway import EventSubClient, TwitchChat
from .models import ChatMessage
from .realtime import ConnectionManager
from .safety import SafetyFilter
from .simulator import ChatSimulator
from .storage import Storage

log = logging.getLogger("chatraid.runtime")
MAX_CMDS_PER_SEC = 100  # channel-level throttle (burst protection)


@dataclass
class Planned:
    plan: DirectorPlan
    starts_at: float | None  # None = waiting for streamer approval


class Runtime:
    def __init__(self, settings: Settings | None = None, llm: Any | None = None) -> None:
        self.s = settings or Settings()
        self.storage = Storage(self.s.db_path)
        self.safety = SafetyFilter()
        self.engine = GameEngine(self.storage, self.safety)
        self.engine.theme = self.s.theme
        self.analyzer = ChatAnalyzer()
        if llm is None:
            client = OpenAIClient(self.s.openai_api_key)
            llm = client if client.available else None
        self.director = Director(self.engine, self.analyzer, self.safety, llm)
        self.manager = ConnectionManager()
        self.vault = TokenVault(self.s, self.storage)
        self.oauth = TwitchOAuth(self.s, self.vault)
        self.chat: TwitchChat | None = None
        self.eventsub: EventSubClient | None = None
        self.sim: ChatSimulator | None = None
        self.planned: Planned | None = None
        self.auto_approve = True
        self.tasks: list[asyncio.Task] = []
        self._cmd_window = (0, 0)  # (second, count)
        self._last_event_end = 0.0
        self._last_pulse_log = 0.0
        self.metrics = {"messages": 0, "commands": 0, "throttled": 0, "events": 0}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self._tick_loop()),
            asyncio.create_task(self._director_loop()),
            asyncio.create_task(self._chat_out_loop()),
            asyncio.create_task(self._pulse_loop()),
        ]
        if self.s.twitch_configured:
            self.chat = TwitchChat(self.s.twitch_channel, self.on_chat, self.s.twitch_bot_nick,
                                   self.s.twitch_bot_token)
            self.tasks.append(asyncio.create_task(self.chat.run()))
            creds = self.vault.load()
            if creds and self.s.twitch_client_id:
                self.eventsub = EventSubClient(self.s.twitch_client_id, creds["access_token"],
                                               creds["twitch_id"], self.on_twitch_event)
                self.tasks.append(asyncio.create_task(self.eventsub.run()))
        if self.s.demo:
            self.engine.start(self.s.theme)
            self.start_simulator("mixed", 25)

    async def stop(self) -> None:
        if self.sim:
            self.sim.stop()
        if self.chat:
            self.chat.stop()
        if self.eventsub:
            self.eventsub.stop()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    # ------------------------------------------------------------------ inputs
    def on_chat(self, msg: ChatMessage) -> None:
        self.metrics["messages"] += 1
        self.analyzer.add(msg)
        if not msg.text.startswith("!"):
            return
        sec = int(time.time())
        if self._cmd_window[0] != sec:
            self._cmd_window = (sec, 0)
        if self._cmd_window[1] >= MAX_CMDS_PER_SEC:
            self.metrics["throttled"] += 1
            return
        self._cmd_window = (sec, self._cmd_window[1] + 1)
        self.metrics["commands"] += 1
        self.engine.handle_command(msg.user_id, msg.display_name, msg.text)

    async def on_twitch_event(self, ev: dict[str, Any]) -> None:
        kind, who = ev["kind"], self.safety.sanitize_name(ev.get("user", "someone"))
        e = self.engine
        if kind == "follow":
            e.community_power += 5
            e._feed(f"{who} followed! +5 community power")
        elif kind == "sub":
            e.community_power += 25
            e._feed(f"{who} subscribed! +25 community power")
            e.say(f"Thanks for the sub, {who}! Chat gains 25 community power.")
        elif kind == "redeem":
            e.community_power += 15
            e._feed(f"{who} redeemed a reward: +15 power")
        elif kind == "raid":
            e._feed(f"RAID from {who} ({ev.get('viewers', 0)} viewers)!")
            e.say(f"Raiders from {who}, welcome! Type !join to enter the fight.")
            await self.trigger_event("raid")
        await self.push_state()

    # ------------------------------------------------------------------ simulator
    def start_simulator(self, scenario: str = "mixed", viewers: int = 20) -> None:
        if self.sim:
            self.sim.stop()
        self.sim = ChatSimulator(self.on_chat, self.engine.snapshot, viewers, scenario)
        self.tasks.append(asyncio.create_task(self.sim.run(seconds=3600)))

    def stop_simulator(self) -> None:
        if self.sim:
            self.sim.stop()
            self.sim = None

    # ------------------------------------------------------------------ director
    async def trigger_event(self, trigger: str = "timer") -> None:
        e = self.engine
        if not e.can_act or (e.event and e.event.status == "active") or self.planned:
            return
        plan = await self.director.next_event(trigger)
        delay = 4.0 if trigger == "raid" else 5.0
        self.planned = Planned(plan, time.time() + delay if self.auto_approve else None)
        await self.manager.to_dashboards({"type": "director_plan", "plan": self.plan_view()})

    def plan_view(self) -> dict[str, Any] | None:
        if not self.planned:
            return None
        p = self.planned
        return {
            "proposal": p.plan.proposal.model_dump(), "source": p.plan.source, "reason": p.plan.reason,
            "difficulty": p.plan.difficulty, "notes": p.plan.notes,
            "starts_in": None if p.starts_at is None else max(0, round(p.starts_at - time.time(), 1)),
            "needs_approval": p.starts_at is None,
        }

    def approve_plan(self) -> bool:
        if self.planned:
            self.planned.starts_at = time.time()
            return True
        return False

    def reject_plan(self) -> bool:
        had = self.planned is not None
        self.planned = None
        self._last_event_end = time.time()
        return had

    async def _director_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                e = self.engine
                active = bool(e.event and e.event.status == "active")
                if active:
                    self._last_event_end = time.time()
                    continue
                if self.planned and self.planned.starts_at is not None and time.time() >= self.planned.starts_at:
                    p, self.planned = self.planned, None
                    if e.start_event(p.plan.proposal, p.plan.source):
                        self.metrics["events"] += 1
                        await self.push_state()
                    continue
                gap = time.time() - self._last_event_end
                if (e.can_act and not self.planned and gap >= self.s.director_interval
                        and self.engine.players):
                    await self.trigger_event("timer")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("director loop error")

    # ------------------------------------------------------------------ loops
    async def push_state(self) -> None:
        snap = self.engine.snapshot()
        await self.manager.to_all({"type": "state_update", "state": snap})

    async def _tick_loop(self) -> None:
        last_push = 0.0
        while True:
            await asyncio.sleep(0.25)
            try:
                changed = self.engine.tick()
                now = time.monotonic()
                if changed or now - last_push >= 0.5:  # overlay updates well under 500ms
                    last_push = now
                    await self.push_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tick error")

    async def _chat_out_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            while self.engine.chat_replies:
                msg = self.engine.chat_replies.popleft()
                if self.chat:
                    await self.chat.say(msg)
                await self.manager.to_dashboards({"type": "chat_out", "text": msg})

    async def _pulse_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            try:
                pulse = self.analyzer.pulse().model_dump()
                await self.manager.to_dashboards({
                    "type": "chat_pulse", "pulse": pulse, "plan": self.plan_view(),
                    "metrics": self.metrics, "director": self.director.stats,
                    "participation": self.participation(), "pending_effect": self.engine.pending_effect})
                if self.engine.storage and self.engine.session_id and time.time() - self._last_pulse_log > 15:
                    self._last_pulse_log = time.time()
                    self.storage.log_pulse(self.engine.session_id, pulse)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pulse error")

    def participation(self) -> float:
        """Share of recent chatters who took at least one game action (success metric > 30%)."""
        chatters = {m.user_id for m in self.analyzer.msgs}
        if not chatters:
            return 0.0
        actors = {uid for uid in chatters if uid in self.engine.players and self.engine.players[uid].actions}
        return round(len(actors) / len(chatters), 2)
