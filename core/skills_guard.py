"""Source code scanner for high-risk patterns in plugin or skill code."""
import ast
import re


DANGEROUS_PATTERNS = [
    (r"\bos\.system\b", "os.system call detected"),
    (r"\bsubprocess\.\w+.*shell\s*=\s*True", "subprocess with shell=True detected"),
    (r"\bsocket\.create_connection\b", "socket network connection detected"),
    (r"\b(eval|exec)\s*\(", "dynamic execution eval/exec detected"),
    (r"\bbase64.*exec\b", "base64 exec chain detected"),
]


def scan_source(code_text: str) -> list[dict]:
    findings = []
    if not isinstance(code_text, str) or not code_text.strip():
        return findings

    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, code_text, flags=re.IGNORECASE):
            findings.append({"risk": "high", "finding": reason})

    return findings
