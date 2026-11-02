from chatraid.engine import ACTION_COOLDOWN, GameEngine
from tests.conftest import join, make_proposal


def test_join_and_class_and_help(engine, clock):
    engine.handle_command("u1", "Sam", "!attack")
    assert "!join first" in engine.chat_replies.pop()
    join(engine, clock, cls="healer")
    assert engine.players["u1"].char_class == "healer"
    engine.handle_command("u1", "Sam", "!help")
    assert "!attack" in engine.chat_replies[-1]


def test_attack_damages_and_awards_points(engine, clock):
    join(engine, clock)
    engine.start_event(make_proposal(enemy_hp=1000))
    engine.handle_command("u1", "Sam", "!attack")
    p = engine.players["u1"]
    assert engine.event.hp < 1000 and p.points == 2 and p.damage > 0


def test_cooldown_blocks_spam(engine, clock):
    join(engine, clock)
    engine.start_event(make_proposal(enemy_hp=5000))
    for _ in range(10):
        engine.handle_command("u1", "Sam", "!attack")
    assert engine.players["u1"].actions == 1
    clock.advance(ACTION_COOLDOWN + 0.1)
    engine.handle_command("u1", "Sam", "!attack")
    assert engine.players["u1"].actions == 2


def test_enemy_defeated_wins_and_rewards(engine, clock):
    join(engine, clock)
    engine.start_event(make_proposal(enemy_hp=50, reward_points=60))
    for _ in range(10):
        engine.handle_command("u1", "Sam", "!attack")
        clock.advance(2.5)
        if engine.event.status != "active":
            break
    assert engine.event.status == "won"
    assert engine.community_power >= 60 and engine.mission["progress"] == 1
    assert engine.history[0]["outcome"] == "won"


def test_timeout_loses_and_enemy_hits_party(engine, clock):
    join(engine, clock)
    engine.start_event(make_proposal(duration_seconds=15, enemy_hp=5000, difficulty=4))
    clock.advance(5.1)
    engine.tick()
    assert engine.party_hp < 100
    clock.advance(10)
    engine.tick()
    assert engine.event.status == "lost"


def test_heal_restores_party_hp(engine, clock):
    join(engine, clock, cls="healer")
    engine.party_hp = 40
    engine.handle_command("u1", "Sam", "!heal")
    assert engine.party_hp == 65 and engine.players["u1"].healing == 25


def test_quest_progress_and_vote(engine, clock):
    join(engine, clock)
    engine.start_event(make_proposal(type="quest", enemy_hp=None, difficulty=1))
    assert engine.event.target == 5
    for _ in range(5):
        engine.handle_command("u1", "Sam", "!attack")
        clock.advance(2.1)
    assert engine.event.status == "won"
    engine.start_event(make_proposal(type="twist", enemy_hp=None))
    engine.handle_command("u1", "Sam", "!vote B")
    assert engine.event.votes == {"u1": "B"}
    clock.advance(61)
    engine.tick()
    assert engine.event.status == "won"


def test_abilities_unlock_and_use(engine, clock):
    join(engine, clock)
    engine.community_power = 70
    engine.handle_command("u1", "Sam", "!unlock shield")
    assert engine.abilities["shield"].unlocked and engine.community_power == 10
    clock.advance(3)
    engine.start_event(make_proposal(enemy_hp=500, difficulty=5))
    engine.handle_command("u1", "Sam", "!use shield")
    assert engine.shield
    clock.advance(5.1)
    engine.tick()
    assert engine.party_hp == 100 and not engine.shield


def test_freeze_stops_enemy(engine, clock):
    join(engine, clock)
    engine.abilities["freeze"].unlocked = True
    engine.start_event(make_proposal(enemy_hp=500, duration_seconds=90))
    engine.handle_command("u1", "Sam", "!use freeze")
    clock.advance(6)
    engine.tick()
    assert engine.party_hp == 100


def test_streamer_effect_needs_approval(engine, clock):
    engine.start_event(make_proposal(streamer_effect="Play with no HUD"))
    assert engine.pending_effect and engine.active_effect is None
    engine.approve_effect(True, 30)
    assert engine.active_effect and engine.pending_effect is None
    clock.advance(31)
    engine.tick()
    assert engine.active_effect is None


def test_pause_blocks_commands_and_kill_switch_clears(engine, clock):
    join(engine, clock)
    engine.start_event(make_proposal(enemy_hp=500))
    engine.pause()
    engine.handle_command("u1", "Sam", "!attack")
    assert engine.players["u1"].actions == 0
    engine.resume()
    engine.kill()
    assert engine.event.status == "skipped" and not engine.feed
    assert engine.start_event(make_proposal()) is None


def test_level_up_and_persistence(clock, storage):
    e = GameEngine(storage, clock=clock)
    e.start()
    join(e, clock)
    e._award(e.players["u1"], 120)
    assert e.players["u1"].level == 3
    e.abilities["dice"].unlocked = True
    e.community_power = 33
    e._save_world()
    e2 = GameEngine(storage, clock=clock)
    assert e2.players["u1"].points == 120 and e2.abilities["dice"].unlocked and e2.community_power == 33


def test_forgetme_deletes_data(engine, clock, storage):
    join(engine, clock)
    engine.handle_command("u1", "Sam", "!forgetme")
    assert "u1" not in engine.players and not storage.load_players()


def test_banned_user_ignored(engine, clock):
    engine.banned.add("u9")
    engine.handle_command("u9", "Troll", "!join")
    assert "u9" not in engine.players


def test_snapshot_shape(engine, clock):
    join(engine, clock)
    engine.start_event(make_proposal())
    s = engine.snapshot()
    assert s["event"]["title"] == "Test Orc" and s["party_hp"] == 100 and len(s["abilities"]) == 4


def test_heal_at_full_hp_is_noop(engine, clock):
    join(engine, clock, cls="healer")
    engine.handle_command("u1", "Sam", "!heal")
    assert engine.players["u1"].points == 0 and engine.players["u1"].actions == 0
