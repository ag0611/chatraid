from chatraid.models import ChatMessage


def msg(clock, text, uid="a"):
    return ChatMessage(user_id=uid, display_name=uid, text=text, ts=clock())


def test_quiet_when_empty(analyzer):
    p = analyzer.pulse()
    assert p.quiet and p.msg_rate == 0


def test_hype_and_sentiment(analyzer, clock):
    for i in range(80):
        analyzer.add(msg(clock, "POG LETS GOOO!! this is amazing", f"u{i % 20}"))
    p = analyzer.pulse()
    assert p.hype > 0.6 and p.sentiment > 0.5 and not p.quiet and p.active_users == 20


def test_negative_and_keywords(analyzer, clock):
    for t in ["this is boring and trash", "boring lag", "rip so bad"]:
        analyzer.add(msg(clock, t))
    p = analyzer.pulse()
    assert p.sentiment < 0 and "boring" in p.top_keywords


def test_spam_detection(analyzer, clock):
    for _ in range(12):
        analyzer.add(msg(clock, "buy followers"))
    assert analyzer.pulse().spam


def test_window_drops_old_messages(analyzer, clock):
    analyzer.add(msg(clock, "hello"))
    clock.advance(25)
    assert analyzer.pulse().msg_rate == 0 and not analyzer.msgs  # raw text is dropped (privacy)
