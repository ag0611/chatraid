"""Chat Analyzer: turns raw messages into a ChatPulse (spec 7.6)."""
from __future__ import annotations

import re
import time
from collections import Counter, deque
from collections.abc import Callable

from .models import ChatMessage, ChatPulse

STOPWORDS = set(
    ["the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "to", "of", "in", "on", "at", "for", "it", "this", "that", "i", "you", "he", "she", "we", "they", "my", "me", "your", "so", "just", "im", "its", "lol", "lmao", "yes", "no", "ok", "okay", "with", "as", "if", "not", "do", "dont", "can", "will", "get", "got"]
)
POSITIVE = set(
    ["pog", "poggers", "pogchamp", "lets", "gg", "wp", "nice", "good", "great", "love", "amazing", "awesome", "win", "won", "hype", "fire", "kappa", "lul", "kreygasm", "hell", "yes", "yay", "clutch", "insane", "epic", "gigachad", "goat"]
)
NEGATIVE = set(
    ["bad", "lose", "lost", "trash", "boring", "cringe", "rip", "sadge", "sad", "f", "dead", "died", "nerf", "laggy", "lag", "worst", "ez", "noob", "mid", "dogwater", "scam"]
)


class ChatAnalyzer:
    """Keeps only a short sliding window of raw messages (privacy: raw text is dropped)."""

    def __init__(self, window: float = 20.0, clock: Callable[[], float] = time.time) -> None:
        self.window = window
        self.now = clock
        self.msgs: deque[ChatMessage] = deque()

    def add(self, msg: ChatMessage) -> None:
        self.msgs.append(msg)
        self._trim()

    def _trim(self) -> None:
        cutoff = self.now() - self.window
        while self.msgs and self.msgs[0].ts < cutoff:
            self.msgs.popleft()

    def recent_texts(self, n: int = 30) -> list[str]:
        self._trim()
        return [m.text for m in list(self.msgs)[-n:] if not m.text.startswith("!")]

    def pulse(self) -> ChatPulse:
        self._trim()
        msgs = list(self.msgs)
        if not msgs:
            return ChatPulse()
        rate = len(msgs) / self.window
        users = {m.user_id for m in msgs}
        words: Counter[str] = Counter()
        pos = neg = 0
        shouting = 0
        texts = Counter()
        for m in msgs:
            texts[m.text.strip().lower()] += 1
            toks = re.findall(r"[a-zA-Z']+", m.text.lower())
            if m.text.isupper() and len(m.text) > 4 or m.text.count("!") >= 2:
                shouting += 1
            for tok in toks:
                if tok in POSITIVE:
                    pos += 1
                if tok in NEGATIVE:
                    neg += 1
                if tok not in STOPWORDS and len(tok) > 2 and not m.text.startswith("!"):
                    words[tok] += 1
        total = pos + neg
        sentiment = (pos - neg) / total if total else 0.0
        hype = min(1.0, rate / 3.0 * 0.6 + shouting / len(msgs) * 0.25 + max(0.0, sentiment) * 0.15)
        top_repeat = texts.most_common(1)[0][1]
        spam = len(msgs) >= 8 and top_repeat / len(msgs) > 0.5
        return ChatPulse(
            msg_rate=round(rate, 2), active_users=len(users), sentiment=round(sentiment, 2),
            hype=round(hype, 2), top_keywords=[w for w, _ in words.most_common(5)],
            quiet=rate < 0.15, spam=spam,
        )
