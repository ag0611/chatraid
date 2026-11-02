# 3-minute demo script

Prep (once): `CHATRAID_DEMO=0`, real channel configured, overlay in OBS, dashboard open on a second monitor,
a friend or two in chat. Keep `python -m chatraid.simulator --scenario hype --viewers 20` ready as backup
traffic. Rehearse 3 times.

| Time | Do | Say |
| --- | --- | --- |
| 0:00-0:20 | Show a normal stream with scrolling chat, then switch to the ChatRaid scene | "Twitch chat is the loudest part of a stream and the least useful. ChatRaid turns it into a game engine." |
| 0:20-1:00 | Friends type `!join warrior/healer`; overlay feed shows them entering | "No installs, no logins - viewers just type." |
| 1:00-1:50 | Director spawns an enemy; chat `!attack` / `!heal` / `!boost`; show the dashboard Director view with its reason ("chat is hyped"); trigger a twist and vote | "The Director reads mood and volume. Hype gets a boss, quiet gets a quest, predictable gets a twist." |
| 1:50-2:20 | `!unlock shield`, `!use shield`; click Accept on a Streamer Challenge | "Chat's heal spam saved the boss fight. Community abilities persist, and anything touching my gameplay needs my approval." |
| 2:20-2:50 | Show the Codex thread, a test it wrote, a bug it fixed (see SUBMISSION.md) | "Here is how Codex built and tested it." |
| 2:50-3:00 | Overlay with leaderboard | "ChatRaid: your chat is the game." |

Honesty note: say "adaptive Director" (rule/signal-driven). The LLM mode exists in code but is not used in
this demo. If you use the simulator for extra traffic, say so on camera; show real Twitch chat commands too.

Safety moment (optional, 10 s): type an injection line in chat and show it never reaches the screen.
