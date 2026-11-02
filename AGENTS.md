# AGENTS.md - ChatRaid

Guidance for coding agents (Codex, Claude Code, etc.) working in this repo.

## What this is
ChatRaid turns Twitch chat into a game. Python 3.12 + FastAPI. An event-driven backend: Twitch Gateway ->
GameEngine (deterministic) -> AI Director (LLM with template fallback) -> WebSocket -> OBS overlay / dashboard.

## Commands
- Install: `pip install -r requirements-dev.txt`
- Test: `pytest -q`  (must stay green; the engine tests use a fake clock - never `sleep` in engine tests)
- Lint: `ruff check .`
- Run with simulated chat, no Twitch needed: `CHATRAID_DEMO=1 python -m chatraid`
- Fake traffic against a running server: `python -m chatraid.simulator --token <admin token> --scenario hype`

## Layout
- `chatraid/engine.py`    game rules, commands, cooldowns, abilities, event lifecycle. Pure + injectable clock/rng.
- `chatraid/director.py`  AI Director: type selection, adaptive difficulty, LLM call, validation, templates.
- `chatraid/safety.py`    SafetyFilter: blocklist, injection/PII/URL detection, proposal validation, chat quoting.
- `chatraid/analyzer.py`  ChatAnalyzer: sliding window -> ChatPulse (drops raw text after 20s).
- `chatraid/gateway.py`   Twitch IRC chat + EventSub WebSocket client and parsers.
- `chatraid/runtime.py`   wiring + background loops (tick, director, chat out, pulse).
- `chatraid/app.py`       FastAPI routes + WebSockets. `static/` holds overlay.html and dashboard.html.
- `chatraid/storage.py`   SQLite persistence. `auth.py` Twitch OAuth + Fernet-encrypted token vault.

## Rules
1. LLM output is untrusted: always go through `SafetyFilter.validate_proposal`; never parse free text.
2. Chat text is untrusted: pass it to an LLM only via `SafetyFilter.quote_for_llm`.
3. Any text that reaches the screen or chat must pass `SafetyFilter.check_text`.
4. Overlay/dashboard JS must set user-derived text with `textContent`, never `innerHTML`.
5. No secrets in the repo; config comes from env vars (`.env.example`).
6. Every feature needs a unit test. Keep the engine deterministic (inject `clock` and `rng`).
7. Anything that affects the streamer's own gameplay needs explicit streamer approval.
