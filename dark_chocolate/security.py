"""Prompt triage and fail-closed human approval primitives."""
from __future__ import annotations
import re
from enum import Enum
from typing import Callable

class GuardVerdict(str, Enum):
    SAFE = "SAFE"
    SUSPICIOUS = "SUSPICIOUS"
    BLOCKED = "BLOCKED"

_PATTERNS = (
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"print\s+the\s+system\s+prompt",
    r"developer\s+message\s*:",
    r"\bact\s+as\s+(an?\s+)?(unrestricted|jailbroken)",
)

def inspect_prompt(text: str) -> tuple[GuardVerdict, tuple[str, ...]]:
    """Heuristic only; clean output is not proof content is safe."""
    if not isinstance(text, str) or not text.strip():
        return GuardVerdict.BLOCKED, ("empty_or_invalid_input",)
    if len(text) > 20_000:
        return GuardVerdict.BLOCKED, ("input_too_large",)
    matched = tuple(p for p in _PATTERNS if re.search(p, text, re.I))
    return (GuardVerdict.SUSPICIOUS, matched) if matched else (GuardVerdict.SAFE, ())

class Risk(str, Enum):
    SAFE = "SAFE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    BLOCKED = "BLOCKED"

def requires_approval(risk: Risk) -> bool:
    return risk in {Risk.HIGH, Risk.CRITICAL}

class ApprovalGate:
    """No callback, callback exception, or non-True response means denial."""
    def __init__(self, prompt: Callable[[str], bool] | None = None) -> None:
        self._prompt = prompt
    def approve(self, reason: str) -> bool:
        if self._prompt is None:
            return False
        try:
            return self._prompt(reason) is True
        except Exception:
            return False
