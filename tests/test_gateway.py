from chatraid.gateway import message_from_irc, normalize_eventsub, parse_irc_line


def test_parse_privmsg_with_tags():
    line = "@badge-info=;display-name=Sam_The_Man;user-id=4242 :sam!sam@sam.tmi.twitch.tv PRIVMSG #chan :!attack now"
    m = message_from_irc(parse_irc_line(line))
    assert (m.user_id, m.display_name, m.text) == ("4242", "Sam_The_Man", "!attack now")


def test_action_message_and_non_privmsg():
    line = "@user-id=1;display-name=A :a!a@a.tmi.twitch.tv PRIVMSG #c :\x01ACTION waves\x01"
    assert message_from_irc(parse_irc_line(line)).text == "waves"
    assert message_from_irc(parse_irc_line(":tmi.twitch.tv 001 nick :Welcome")) is None


def test_message_with_colon_in_text():
    line = "@user-id=1;display-name=A :a!a@a.tmi.twitch.tv PRIVMSG #c :hello :) : test"
    assert message_from_irc(parse_irc_line(line)).text == "hello :) : test"


def test_eventsub_normalisation():
    assert normalize_eventsub("channel.follow", {"user_name": "Z"}) == {"kind": "follow", "user": "Z"}
    assert normalize_eventsub("channel.raid", {"from_broadcaster_user_name": "R", "viewers": 40}) == \
        {"kind": "raid", "user": "R", "viewers": 40}
    r = normalize_eventsub("channel.channel_points_custom_reward_redemption.add",
                           {"user_name": "Q", "reward": {"title": "Boss"}})
    assert r["kind"] == "redeem" and r["reward"] == "Boss"
    assert normalize_eventsub("channel.unknown", {}) is None
