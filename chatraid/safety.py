"""Safety filter for viewer input and AI output (FR9)."""
from __future__ import annotations

import re

from pydantic import ValidationError

from .models import EventProposal

# Deliberately small built-in list; streamers extend it via the blocklist setting.
DEFAULT_BLOCKLIST = {"slur1", "slur2", "kys", "doxx", "dox"}

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"you are now",
    r"system prompt",
    r"reveal (your|the) (prompt|instructions)",
    r"act as (an? )?(unrestricted|jailbroken)",
    r"</?(system|assistant|tool)>",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)
_PII_RE = re.compile(
    r"(\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b)|([\w.+-]+@[\w-]+\.[\w.-]+)", re.IGNORECASE
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class SafetyFilter:
    def __init__(self, blocklist: set[str] | None = None) -> None:
        self.blocklist: set[str] = {w.lower() for w in (blocklist or set())} | DEFAULT_BLOCKLIST

    def add_blocked(self, word: str) -> None:
        self.blocklist.add(word.lower().strip())

    def set_blocklist(self, words: list[str]) -> None:
        self.blocklist = {w.lower().strip() for w in words if w.strip()} | DEFAULT_BLOCKLIST

    def _has_blocked(self, text: str) -> bool:
        low = text.lower()
        tokens = set(re.findall(r"[a-z0-9']+", low))
        return any((w in tokens) or (" " in w and w in low) for w in self.blocklist)

    def is_injection(self, text: str) -> bool:
        return bool(_INJECTION_RE.search(text))

    def check_text(self, text: str) -> tuple[bool, str]:
        """Return (ok, reason). Used for anything that reaches the screen."""
        if self._has_blocked(text):
            return False, "blocked_word"
        if self.is_injection(text):
            return False, "prompt_injection"
        if _PII_RE.search(text):
            return False, "personal_data"
        if _URL_RE.search(text):
            return False, "url"
        return True, ""

    def sanitize_name(self, name: str) -> str:
        ok, _ = self.check_text(name)
        if not ok:
            return "viewer"
        return re.sub(r"[^\w\- ]", "", name)[:25] or "viewer"

    def quote_for_llm(self, messages: list[str], limit: int = 30) -> str:
        """Wrap untrusted chat as quoted data (prompt-injection defense)."""
        safe = []
        for m in messages[-limit:]:
            m = m.replace("<", "(").replace(">", ")")[:120]
            if self.is_injection(m) or self._has_blocked(m):
                m = "[filtered]"
            safe.append(m)
        body = "\n".join(f"- {m}" for m in safe)
        return f"<untrusted_chat>\n{body}\n</untrusted_chat>"

    def validate_proposal(
        self, raw: dict | EventProposal, max_difficulty: int = 10
    ) -> tuple[EventProposal | None, str]:
        """Schema + content + difficulty validation of Director output."""
        try:
            prop = raw if isinstance(raw, EventProposal) else EventProposal.model_validate(raw)
        except ValidationError as exc:
            return None, f"schema: {exc.errors()[0]['msg']}"
        for field in (prop.title, prop.description, prop.success_condition, prop.streamer_effect or ""):
            ok, why = self.check_text(field)
            if not ok:
                return None, f"content: {why}"
        if prop.difficulty > max_difficulty:
            prop = prop.model_copy(update={"difficulty": max_difficulty})
        if prop.type == "enemy" and prop.enemy_hp is None:
            prop = prop.model_copy(update={"enemy_hp": 100 + prop.difficulty * 60})
        return prop, ""
