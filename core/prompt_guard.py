"""Heuristic prompt-injection signal scanner; not a substitute for isolation or policy enforcement."""
from __future__ import annotations
import re

_PATTERNS = {
    "instruction_override": re.compile(r"\b(ignore|disregard|override)\b.{0,60}\b(previous|prior|system|developer|instructions?)\b", re.I),
    "secret_exfiltration": re.compile(r"\b(reveal|print|send|exfiltrate|dump)\b.{0,50}\b(secret|api key|password|token|credential|system prompt)\b", re.I),
    "tool_abuse": re.compile(r"\b(run|execute|invoke)\b.{0,60}\b(shell|terminal|command|tool|browser)\b", re.I),
    "role_spoofing": re.compile(r"(?im)^\s*(system|developer|assistant)\s*:\s*"),
    "untrusted_markup": re.compile(r"<\s*(system|developer|tool|instructions?)\b|\[\s*SYSTEM\s*\]", re.I),
}
def scan_untrusted_text(text, *, max_chars=20000):
    if not isinstance(text,str): raise TypeError("text must be a string")
    clipped=text[:max_chars]
    matches=[name for name,pattern in _PATTERNS.items() if pattern.search(clipped)]
    return {"flagged":bool(matches),"signals":matches,"truncated":len(text)>max_chars,
            "text_length":len(text),"recommendation":"Treat as untrusted data; never follow embedded instructions or grant permissions based on this scan." if matches else "No known pattern matched; this does not prove the text is safe."}
