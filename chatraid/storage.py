"""SQLite persistence (stdlib sqlite3, PostgreSQL-ready schema)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS players(
  twitch_user_id TEXT PRIMARY KEY, display_name TEXT, total_points INTEGER DEFAULT 0,
  level INTEGER DEFAULT 1, class TEXT DEFAULT 'warrior', first_seen_at REAL);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL, ended_at REAL,
  theme TEXT, difficulty INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS game_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, type TEXT, title TEXT,
  payload_json TEXT, difficulty INTEGER, source TEXT, started_at REAL, ended_at REAL, outcome TEXT);
CREATE TABLE IF NOT EXISTS action_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, user_id TEXT, command TEXT,
  args TEXT, result TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS world(key TEXT PRIMARY KEY, value_json TEXT);
CREATE TABLE IF NOT EXISTS chat_pulse(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, ts REAL, msg_rate REAL,
  sentiment REAL, hype REAL, top_keywords_json TEXT);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, role TEXT, rating INTEGER,
  comment TEXT, created_at REAL);
"""


class Storage:
    def __init__(self, path: str = "chatraid.db") -> None:
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            # WAL + NORMAL avoids an fsync on every commit (large speedup, esp. on Windows)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            self.db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # players ---------------------------------------------------------
    def upsert_player(self, uid: str, name: str, points: int, level: int, cls: str) -> None:
        self._exec(
            """INSERT INTO players(twitch_user_id,display_name,total_points,level,class,first_seen_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(twitch_user_id) DO UPDATE SET
               display_name=excluded.display_name,total_points=excluded.total_points,
               level=excluded.level,class=excluded.class""",
            (uid, name, points, level, cls, time.time()),
        )

    def load_players(self) -> list[sqlite3.Row]:
        return self._exec("SELECT * FROM players").fetchall()

    def delete_player(self, uid: str) -> None:
        self._exec("DELETE FROM players WHERE twitch_user_id=?", (uid,))
        self._exec("DELETE FROM action_log WHERE user_id=?", (uid,))

    def leaderboard(self, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT display_name,total_points,level FROM players ORDER BY total_points DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]

    # world -----------------------------------------------------------
    def set_world(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO world(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value)),
        )

    def get_world(self, key: str, default: Any = None) -> Any:
        row = self._exec("SELECT value_json FROM world WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    # sessions / events / logs ---------------------------------------
    def start_session(self, theme: str, difficulty: int) -> int:
        cur = self._exec(
            "INSERT INTO sessions(started_at,theme,difficulty,status) VALUES(?,?,?,'running')",
            (time.time(), theme, difficulty),
        )
        return int(cur.lastrowid)

    def end_session(self, sid: int) -> None:
        self._exec("UPDATE sessions SET ended_at=?,status='ended' WHERE id=?", (time.time(), sid))

    def log_event(self, sid: int, ev: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO game_events(session_id,type,title,payload_json,difficulty,source,started_at,ended_at,outcome)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (sid, ev["type"], ev["title"], json.dumps(ev["payload"]), ev["difficulty"],
             ev["source"], ev["started_at"], time.time(), ev["outcome"]),
        )

    def log_action(self, sid: int, uid: str, command: str, args: str, result: str) -> None:
        self._exec(
            "INSERT INTO action_log(session_id,user_id,command,args,result,created_at) VALUES(?,?,?,?,?,?)",
            (sid, uid, command, args, result, time.time()),
        )

    def log_pulse(self, sid: int, p: dict[str, Any]) -> None:
        self._exec(
            "INSERT INTO chat_pulse(session_id,ts,msg_rate,sentiment,hype,top_keywords_json) VALUES(?,?,?,?,?,?)",
            (sid, time.time(), p["msg_rate"], p["sentiment"], p["hype"], json.dumps(p["top_keywords"])),
        )

    def add_feedback(self, sid: int, role: str, rating: int, comment: str) -> None:
        self._exec(
            "INSERT INTO feedback(session_id,role,rating,comment,created_at) VALUES(?,?,?,?,?)",
            (sid, role, rating, comment[:500], time.time()),
        )

    def recent_events(self, sid: int, n: int = 10) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT type,title,difficulty,source,outcome FROM game_events WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (sid, n),
        ).fetchall()
        return [dict(r) for r in rows]

    def recap(self, sid: int) -> dict[str, Any]:
        ev = self._exec(
            "SELECT outcome, COUNT(*) c FROM game_events WHERE session_id=? GROUP BY outcome", (sid,)
        ).fetchall()
        acts = self._exec("SELECT COUNT(*) c FROM action_log WHERE session_id=?", (sid,)).fetchone()["c"]
        fb = self._exec("SELECT AVG(rating) a FROM feedback WHERE session_id=?", (sid,)).fetchone()["a"]
        return {"events": {r["outcome"]: r["c"] for r in ev}, "actions": acts, "avg_rating": fb}
