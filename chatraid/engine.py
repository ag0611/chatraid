"""Deterministic game engine: processes commands, applies rules, ticks time."""
from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .models import Ability, ActiveEvent, EventProposal, Player
from .safety import SafetyFilter
from .storage import Storage

ACTION_COOLDOWN = 2.0  # seconds per user (spec 7.9)
BOOST_COOLDOWN = 30.0
BOOST_SECONDS = 10
ENEMY_TICK = 5.0
PARTY_MAX_HP = 100
CLASSES = ("warrior", "healer", "trickster")

DEFAULT_ABILITIES = [
    Ability(key="shield", name="Shield Wall", description="Blocks the next hit", cost=60),
    Ability(key="dice", name="Chaos Dice", description="Random bonus or twist", cost=40),
    Ability(key="freeze", name="Time Freeze", description="Pause the enemy for 20s", cost=90),
    Ability(key="challenge", name="Streamer Challenge", description="A timed rule for the streamer", cost=120),
]


class GameEngine:
    def __init__(
        self,
        storage: Storage | None = None,
        safety: SafetyFilter | None = None,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.storage = storage
        self.safety = safety or SafetyFilter()
        self.now = clock
        self.rng = rng or random.Random()

        self.status: str = "idle"  # idle | running | paused | ended
        self.theme = "fantasy"
        self.difficulty = 3
        self.session_id = 0
        self.players: dict[str, Player] = {}
        self.banned: set[str] = set()
        self.abilities: dict[str, Ability] = {a.key: a.model_copy() for a in DEFAULT_ABILITIES}
        self.community_power = 0
        self.party_hp = PARTY_MAX_HP
        self.event: ActiveEvent | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=10)
        self.feed: deque[str] = deque(maxlen=8)
        self.chat_replies: deque[str] = deque()  # outbound chat messages
        self.next_event_id = 1
        self.cooldowns: dict[tuple[str, str], float] = {}
        self.boost_until = 0.0
        self.shield = False
        self.freeze_until = 0.0
        self.last_enemy_tick = 0.0
        self.pending_effect: str | None = None
        self.active_effect: tuple[str, float] | None = None
        self.mission = {"goal": "Defeat 3 threats together", "progress": 0, "target": 3, "reward": 80}
        self.kill_switch = False
        self.last_win_streak = 0
        self._load_world()

    # ------------------------------------------------------------------ persistence
    def _load_world(self) -> None:
        if not self.storage:
            return
        for row in self.storage.load_players():
            self.players[row["twitch_user_id"]] = Player(
                user_id=row["twitch_user_id"], display_name=row["display_name"],
                points=row["total_points"], level=row["level"], char_class=row["class"],
            )
        w = self.storage.get_world("community", {})
        self.community_power = w.get("power", 0)
        for k in w.get("unlocked", []):
            if k in self.abilities:
                self.abilities[k].unlocked = True
        self.mission = w.get("mission", self.mission)

    def _save_world(self) -> None:
        if not self.storage:
            return
        self.storage.set_world("community", {
            "power": self.community_power,
            "unlocked": [k for k, a in self.abilities.items() if a.unlocked],
            "mission": self.mission,
        })

    def _save_player(self, p: Player) -> None:
        if self.storage:
            self.storage.upsert_player(p.user_id, p.display_name, p.points, p.level, p.char_class)

    # ------------------------------------------------------------------ session control
    def start(self, theme: str | None = None, difficulty: int | None = None) -> None:
        self.theme = theme or self.theme
        self.difficulty = max(1, min(10, difficulty or self.difficulty))
        self.status = "running"
        self.kill_switch = False
        self.party_hp = PARTY_MAX_HP
        self.event = None
        self.history.clear()
        if self.storage:
            self.session_id = self.storage.start_session(self.theme, self.difficulty)
        self._feed("Session started - type !join to enter the raid!")
        self.say("ChatRaid is live! Type !join [warrior|healer|trickster] to play. !help for commands.")

    def pause(self) -> None:
        if self.status == "running":
            self.status = "paused"
            self._feed("Session paused")

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "running"
            if self.event:  # don't punish chat for the pause
                self.event.ends_at += 1
            self.last_enemy_tick = self.now()
            self._feed("Session resumed")

    def end(self) -> None:
        if self.event:
            self._resolve("lost" if self.event.status == "active" else self.event.status)
        self.status = "ended"
        self._save_world()
        if self.storage and self.session_id:
            self.storage.end_session(self.session_id)
        self._feed("Session ended - thanks for playing!")

    def kill(self) -> None:
        """Kill switch: stop AI events and clear the overlay immediately."""
        self.kill_switch = True
        if self.event:
            self.event.status = "skipped"
            self._resolve("skipped")
        self.feed.clear()
        self.pending_effect = None
        self.active_effect = None

    def set_difficulty(self, level: int) -> None:
        self.difficulty = max(1, min(10, level))

    # ------------------------------------------------------------------ helpers
    def say(self, msg: str) -> None:
        ok, _ = self.safety.check_text(msg)
        if ok:
            self.chat_replies.append(msg[:400])

    def _feed(self, msg: str) -> None:
        self.feed.appendleft(msg[:90])

    def _cooldown_ok(self, uid: str, cmd: str, seconds: float) -> bool:
        key = (uid, cmd)
        t = self.now()
        if self.cooldowns.get(key, 0) > t:
            return False
        self.cooldowns[key] = t + seconds
        return True

    def _award(self, p: Player, pts: int, community: bool = True) -> None:
        p.points += pts
        if community:
            self.community_power += pts
        new_level = 1 + p.points // 50
        if new_level > p.level:
            p.level = new_level
            self._feed(f"LEVEL UP! {p.display_name} reached level {p.level}")
            self.say(f"@{p.display_name} reached level {p.level}!")
        self._save_player(p)

    @property
    def can_act(self) -> bool:
        return self.status == "running" and not self.kill_switch

    # ------------------------------------------------------------------ commands
    def handle_command(self, uid: str, display_name: str, text: str) -> None:
        """Entry point for every chat line that starts with '!'. Replies go to chat_replies."""
        if uid in self.banned:
            return
        parts = text.strip().split()
        if not parts or not parts[0].startswith("!"):
            return
        cmd, args = parts[0][1:].lower(), parts[1:]
        name = self.safety.sanitize_name(display_name)

        if cmd == "help":
            self.say("Commands: !join [class] !attack !heal !boost !vote A|B !me !unlock <ability> !use <ability>")
            return
        if cmd == "join":
            self._join(uid, name, args)
            return
        if cmd == "forgetme":
            self.players.pop(uid, None)
            if self.storage:
                self.storage.delete_player(uid)
            self.say(f"@{name} your ChatRaid data was deleted.")
            return

        p = self.players.get(uid)
        if not p:
            self.say(f"@{name} type !join first!")
            return
        if cmd == "me":
            self.say(f"@{p.display_name}: {p.char_class} lvl {p.level}, {p.points} pts, {p.actions} actions, "
                     f"{p.damage} dmg, {p.healing} healed")
            return
        if cmd not in ("attack", "heal", "boost", "vote", "unlock", "use"):
            return
        if not self.can_act:
            return
        if not self._cooldown_ok(uid, "any", ACTION_COOLDOWN):
            return  # silently rate-limited
        action = {
            "attack": self._attack, "heal": self._heal, "boost": self._boost,
            "vote": self._vote, "unlock": self._unlock, "use": self._use,
        }[cmd]
        result = action(p, args)
        if self.storage and self.session_id:
            self.storage.log_action(self.session_id, uid, cmd, " ".join(args), result or "")

    def _join(self, uid: str, name: str, args: list[str]) -> None:
        cls = args[0].lower() if args and args[0].lower() in CLASSES else None
        p = self.players.get(uid)
        if p:
            if cls:
                p.char_class = cls  # type: ignore[assignment]
                self._save_player(p)
                self.say(f"@{p.display_name} is now a {cls}.")
            else:
                self.say(f"@{p.display_name} you're already in the raid.")
            return
        p = Player(user_id=uid, display_name=name, char_class=cls or "warrior")  # type: ignore[arg-type]
        self.players[uid] = p
        self._save_player(p)
        self._feed(f"{name} joined as {p.char_class}")
        self.say(f"Welcome @{name}! You are a {p.char_class}. Use !attack, !heal, !boost, !vote. (!help)")

    def _attack(self, p: Player, args: list[str]) -> str:
        ev = self.event
        if not ev or ev.status != "active":
            return "no_event"
        if ev.proposal.type in ("enemy",):
            base = {"warrior": (20, 30), "healer": (8, 14), "trickster": (12, 24)}[p.char_class]
            dmg = self.rng.randint(*base)
            if self.now() < self.boost_until:
                dmg = int(dmg * 1.5)
            ev.hp = max(0, ev.hp - dmg)
            p.damage += dmg
            p.actions += 1
            self._award(p, 2)
            self._feed(f"{p.display_name} hit for {dmg}")
            if ev.hp <= 0:
                self._resolve("won")
            return f"dmg {dmg}"
        return self._progress(p, "helped")

    def _heal(self, p: Player, args: list[str]) -> str:
        amount = {"warrior": 10, "healer": 25, "trickster": 14}[p.char_class]
        before = self.party_hp
        self.party_hp = min(PARTY_MAX_HP, self.party_hp + amount)
        gained = self.party_hp - before
        ev0 = self.event
        hazard = bool(ev0 and ev0.status == "active" and ev0.proposal.type == "hazard")
        if gained == 0 and not hazard:
            return "full_hp"  # nothing to heal: no points, no feed spam
        p.healing += gained
        p.actions += 1
        self._award(p, 2)
        self._feed(f"{p.display_name} healed +{gained}")
        ev = self.event
        if ev and ev.status == "active" and ev.proposal.type == "hazard":
            return self._progress(p, "stabilised")
        return f"heal {gained}"

    def _boost(self, p: Player, args: list[str]) -> str:
        if not self._cooldown_ok("team", "boost", BOOST_COOLDOWN):
            return "boost_cooldown"
        self.boost_until = self.now() + BOOST_SECONDS
        p.actions += 1
        self._award(p, 3)
        self._feed(f"{p.display_name} BOOSTED the team x1.5 for {BOOST_SECONDS}s")
        return "boost"

    def _vote(self, p: Player, args: list[str]) -> str:
        ev = self.event
        opt = args[0].upper() if args else ""
        if not ev or ev.status != "active" or opt not in ("A", "B"):
            return "bad_vote"
        first = p.user_id not in ev.votes
        ev.votes[p.user_id] = opt
        if first:
            p.actions += 1
            self._award(p, 1)
            if ev.proposal.type in ("quest", "community_goal"):
                ev.progress += 1
                if ev.progress >= ev.target:
                    self._resolve("won")
        return f"vote {opt}"

    def _progress(self, p: Player, verb: str) -> str:
        ev = self.event
        if not ev or ev.status != "active":
            return "no_event"
        if ev.proposal.type in ("quest", "community_goal", "hazard"):
            ev.progress += 1
            p.actions += 1
            self._award(p, 2)
            self._feed(f"{p.display_name} {verb} (+1 progress)")
            if ev.progress >= ev.target:
                self._resolve("won")
        return "progress"

    def _unlock(self, p: Player, args: list[str]) -> str:
        key = args[0].lower() if args else ""
        ab = self.abilities.get(key)
        if not ab or ab.unlocked:
            self.say("Unknown or already unlocked. Options: " + ", ".join(
                f"{a.key}({a.cost})" for a in self.abilities.values() if not a.unlocked))
            return "bad_unlock"
        if self.community_power < ab.cost:
            self.say(f"Need {ab.cost} community power (have {self.community_power}).")
            return "too_poor"
        self.community_power -= ab.cost
        ab.unlocked = True
        self._save_world()
        self._feed(f"UNLOCKED {ab.name} (by {p.display_name})")
        self.say(f"Chat unlocked {ab.name}! Use it with !use {ab.key}")
        return "unlocked"

    def _use(self, p: Player, args: list[str]) -> str:
        key = args[0].lower() if args else ""
        ab = self.abilities.get(key)
        if not ab or not ab.unlocked:
            return "locked"
        if not self._cooldown_ok("team", f"use_{key}", 45):
            return "cooldown"
        t = self.now()
        if key == "shield":
            self.shield = True
            self._feed(f"{p.display_name} raised Shield Wall")
        elif key == "freeze":
            self.freeze_until = t + 20
            self._feed(f"{p.display_name} froze time for 20s")
        elif key == "dice":
            roll = self.rng.choice(["bonus", "heal", "damage", "chaos"])
            if roll == "heal":
                self.party_hp = min(PARTY_MAX_HP, self.party_hp + 30)
            elif roll == "damage" and self.event and self.event.proposal.type == "enemy":
                self.event.hp = max(0, self.event.hp - 80)
                if self.event.hp <= 0:
                    self._resolve("won")
            elif roll == "bonus":
                self.community_power += 20
            else:
                self.party_hp = max(1, self.party_hp - 10)
            self._feed(f"Chaos Dice rolled: {roll}!")
        elif key == "challenge":
            self.pending_effect = "Streamer must play one-handed for 60 seconds"
            self._feed("Streamer challenge proposed - awaiting approval")
        p.actions += 1
        self._award(p, 1)
        return f"use {key}"

    # ------------------------------------------------------------------ events
    def start_event(self, proposal: EventProposal, source: str = "director") -> ActiveEvent | None:
        if not self.can_act or self.event and self.event.status == "active":
            return None
        t = self.now()
        ev = ActiveEvent(
            id=self.next_event_id, proposal=proposal, source=source,  # type: ignore[arg-type]
            started_at=t, ends_at=t + proposal.duration_seconds,
        )
        self.next_event_id += 1
        if proposal.type == "enemy":
            ev.hp = ev.max_hp = proposal.enemy_hp or 200
        elif proposal.type in ("quest", "community_goal", "hazard"):
            ev.target = max(5, proposal.difficulty * 4)
        self.event = ev
        self.last_enemy_tick = t
        self.shield = False
        if proposal.streamer_effect:
            self.pending_effect = proposal.streamer_effect
        self._feed(f"NEW {proposal.type.upper()}: {proposal.title}")
        self.say(f"[{proposal.type.upper()}] {proposal.title} - {proposal.description}")
        return ev

    def skip_event(self) -> None:
        if self.event and self.event.status == "active":
            self.event.status = "skipped"
            self._resolve("skipped")

    def approve_effect(self, approve: bool, seconds: int = 60) -> None:
        if self.pending_effect and approve:
            self.active_effect = (self.pending_effect, self.now() + seconds)
            self._feed("Streamer accepted the challenge!")
        self.pending_effect = None

    def _resolve(self, outcome: str) -> None:
        ev = self.event
        if not ev:
            return
        if ev.proposal.type == "twist" and outcome != "skipped":
            outcome = self._resolve_twist(ev)
        ev.status = outcome  # type: ignore[assignment]
        p = ev.proposal
        if outcome == "won":
            self.last_win_streak += 1
            for pl in self.players.values():
                if pl.actions:
                    self._award(pl, p.reward_points // 5, community=False)  # personal bonus only
            self.community_power += p.reward_points
            self.mission["progress"] += 1
            if self.mission["progress"] >= self.mission["target"]:
                self.community_power += self.mission["reward"]
                self._feed(f"MISSION COMPLETE! +{self.mission['reward']} community power")
                self.mission["progress"] = 0
                self.mission["target"] += 1
            self._feed(f"Victory! {p.title} (+{p.reward_points})")
            self.say(f"Victory over '{p.title}'! +{p.reward_points} community power.")
        elif outcome == "lost":
            self.last_win_streak = 0
            self.party_hp = max(30, self.party_hp - 10)
            self._feed(f"Defeat... {p.title} survived")
            self.say(f"'{p.title}' defeated chat this time. Regroup!")
        self.history.appendleft({
            "type": p.type, "title": p.title, "difficulty": p.difficulty, "outcome": outcome,
            "duration": round(self.now() - ev.started_at, 1),
        })
        if self.storage and self.session_id:
            self.storage.log_event(self.session_id, {
                "type": p.type, "title": p.title, "payload": p.model_dump(), "difficulty": p.difficulty,
                "source": ev.source, "started_at": ev.started_at, "outcome": outcome,
            })
        self._save_world()

    def _resolve_twist(self, ev: ActiveEvent) -> str:
        a = sum(1 for v in ev.votes.values() if v == "A")
        b = sum(1 for v in ev.votes.values() if v == "B")
        if not ev.votes:
            return "lost"
        winner = "A" if a >= b else "B"
        self._feed(f"Vote result: option {winner} ({a} vs {b})")
        return "won"

    def tick(self) -> bool:
        """Advance time. Returns True if state changed."""
        if self.status != "running":
            return False
        t = self.now()
        changed = False
        if self.active_effect and t >= self.active_effect[1]:
            self.active_effect = None
            changed = True
        ev = self.event
        if not ev or ev.status != "active":
            return changed
        if t >= ev.ends_at:
            self._resolve("lost" if ev.proposal.type != "twist" else "won")
            return True
        if t - self.last_enemy_tick >= ENEMY_TICK and t >= self.freeze_until:
            self.last_enemy_tick = t
            if ev.proposal.type in ("enemy", "hazard"):
                if self.shield:
                    self.shield = False
                    self._feed("Shield Wall blocked the hit!")
                else:
                    dmg = ev.proposal.difficulty * 2 + 2
                    self.party_hp = max(0, self.party_hp - dmg)
                    self._feed(f"{ev.proposal.title} hits the party for {dmg}")
                if self.party_hp <= 0:
                    self.party_hp = 40
                    self._resolve("lost")
            changed = True
        return changed

    # ------------------------------------------------------------------ views
    def top_players(self, n: int = 5) -> list[dict[str, Any]]:
        ranked = sorted(self.players.values(), key=lambda p: p.points, reverse=True)[:n]
        return [{"name": p.display_name, "points": p.points, "level": p.level, "class": p.char_class}
                for p in ranked]

    def snapshot(self) -> dict[str, Any]:
        t = self.now()
        ev = self.event
        event_view = None
        if ev:
            event_view = {
                "id": ev.id, "type": ev.proposal.type, "title": ev.proposal.title,
                "description": ev.proposal.description, "status": ev.status, "source": ev.source,
                "time_left": max(0, round(ev.ends_at - t)), "duration": ev.proposal.duration_seconds,
                "hp": ev.hp, "max_hp": ev.max_hp, "progress": ev.progress, "target": ev.target,
                "difficulty": ev.proposal.difficulty,
                "votes": {"A": sum(v == "A" for v in ev.votes.values()),
                          "B": sum(v == "B" for v in ev.votes.values())},
            }
        return {
            "status": self.status, "theme": self.theme, "difficulty": self.difficulty,
            "party_hp": self.party_hp, "party_max_hp": PARTY_MAX_HP,
            "community_power": self.community_power, "event": event_view,
            "mission": self.mission, "feed": list(self.feed), "top": self.top_players(5),
            "players": len(self.players), "boost": max(0, round(self.boost_until - t)),
            "shield": self.shield, "frozen": max(0, round(self.freeze_until - t)),
            "abilities": [a.model_dump() for a in self.abilities.values()],
            "effect": {"text": self.active_effect[0], "left": max(0, round(self.active_effect[1] - t))}
            if self.active_effect else None,
            "kill_switch": self.kill_switch,
            "commands": "!join  !attack  !heal  !boost  !vote A/B",
        }
