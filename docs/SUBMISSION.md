# Devpost submission pack

## Checklist (deadline: Nov 3, 2026, 3:30 AM IST - submit hours early)
- [ ] GitHub repo public (or shared with testing@devpost.com), README with run instructions
- [ ] Demo video <= 3 min, public on YouTube, shows Twitch + Codex usage
- [ ] Project description (below, edit to match what you actually did; the demo uses the template Director, so say "adaptive Director", not "LLM")
- [ ] **Codex session ID** from `/status` in the Codex thread you actually used
- [ ] Devpost project created, all fields filled, submitted

## IMPORTANT - Codex requirement
The hackathon requires the project to be built with OpenAI Codex and a Codex session ID. This code base was
generated outside Codex, so do not claim otherwise. To make the submission truthful: open this repo in
Codex (`AGENTS.md` is already there), and do real work in one thread - e.g. run live-Twitch smoke tests and
fix what breaks, add Playwright UI tests, add the Twitch Extension, refactor. Then run `/status`, copy the
session ID, and describe only what Codex really did.

## Project description (draft)
**ChatRaid turns Twitch chat into a game engine.** Chat is usually passive - messages scroll away and the
streamer can't read them. ChatRaid listens to chat and EventSub (follows, subs, raids, channel-point
redemptions), and an adaptive Director converts what chat is doing into a live game: enemies when chat is hyped,
quests when it's quiet, twists when it gets predictable, hazards when the party is hurt.

Viewers play only by typing (`!join`, `!attack`, `!heal`, `!boost`, `!vote`), earn points, and pool them to
unlock community abilities that persist across streams. A browser-source overlay shows consequences in
real time; a dashboard gives the streamer pause/skip/approve controls, difficulty, theme, blocklist and a
kill switch. Streamer-affecting effects require explicit approval.

**How it's built:** Python/FastAPI event-driven backend, deterministic game engine, a signal-driven Director
(reads message rate, mood, hype, spam and party health; adapts difficulty from win/loss streaks; never
repeats event types) with a template library so the stream never stalls. An optional LLM mode (OpenAI
structured outputs, schema-validated and clamped in code) is built in but was not used in the demo.
Layered safety (input/output filters, prompt-injection defence, schema validation), WebSocket
overlay/dashboard, SQLite persistence (WAL), 56 automated tests including a 300-message burst test, and a
chat simulator for load and demos.

**How Codex was used:** <fill in honestly from your Codex thread>

**What's next:** Twitch Extension, multi-streamer raids as in-game battles, TTS narration, analytics.
