"""Conservative output checks; this is a policy helper, not a security proof."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    text: str
    reasons: tuple[str, ...]


_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PRIVATE )?PRIVATE KEY-----", re.I)),
    ("credential_assignment", re.compile(r"""(?i)["']?\b(api[_-]?key|access[_-]?token|password|client_secret)["']?\s*[:=]\s*["']?[^\s,;}'"]+""")),
    ("unsafe_claim", re.compile(r"(?i)\b(i have|i've) (?:successfully )?(?:deleted|sent|purchased|transferred|executed)\b")),
)


class OutputGuard:
    def __init__(self, *, max_chars: int = 20000, block_credential_patterns: bool = True):
        if isinstance(max_chars, bool) or max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self.block_credential_patterns = block_credential_patterns

    def check(self, text: object) -> GuardResult:
        value = str(text)
        reasons = []
        if len(value) > self.max_chars:
            reasons.append("output_too_long")
        if any(pattern.search(value) for name, pattern in _PATTERNS if name == "private_key"):
            reasons.append("private_key_material")
        if self.block_credential_patterns and _PATTERNS[1][1].search(value):
            reasons.append("possible_credential_disclosure")
        # Unverified completion claims are flagged for review, not categorically blocked.
        if _PATTERNS[2][1].search(value):
            reasons.append("unverified_action_claim_review")
        if "output_too_long" in reasons:
            value = value[:self.max_chars] + "\n[Output truncated by policy.]"
        blocked = any(reason in reasons for reason in ("private_key_material", "possible_credential_disclosure"))
        if blocked:
            value = "[Output withheld by safety policy.]"
        return GuardResult(not blocked, value, tuple(reasons))
