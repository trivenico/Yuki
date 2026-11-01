"""Bounded, redacting secret scanner for authorized local repository audits.

Never prints matched credential values. Heuristic detection only; false positives
and false negatives are expected. Does not follow symlinks or scan binary/large files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"}
PATTERNS = (
    ("YSEC101", "high", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("YSEC102", "high", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("YSEC103", "critical", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("YSEC104", "high", re.compile(r"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*['\"]?([A-Za-z0-9_./+=-]{20,})")),
)

@dataclass(frozen=True)
class SecretFinding:
    rule_id: str
    severity: str
    path: str
    line: int
    fingerprint: str
    redacted_match: str = "[REDACTED]"

def scan(root: Path) -> tuple[list[SecretFinding], list[dict[str, str]]]:
    root = root.resolve()
    findings: list[SecretFinding] = []
    errors: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for line_number, line in enumerate(content.splitlines(), 1):
            for rule_id, severity, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    value = match.group(1) if match.lastindex else match.group(0)
                    fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
                    findings.append(SecretFinding(rule_id, severity, path.relative_to(root).as_posix(), line_number, fingerprint))
    findings.sort(key=lambda f: (f.path, f.line, f.rule_id, f.fingerprint))
    return findings, errors

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--fail-on-findings", action="store_true", help="Return a failing exit code when any heuristic match is found.")
    args = parser.parse_args(argv)
    findings, errors = scan(args.root)
    report = {"schema_version": 1, "scanner": "yuki-redacted-secret-auditor",
              "finding_count": len(findings), "findings": [asdict(f) for f in findings],
              "limitations": ["Heuristic detection only; validate findings securely.", "Matched values are never emitted."]}
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(f"Yuki secret audit: {len(findings)} finding(s)")
        for finding in findings:
            print(f"{finding.severity.upper()} {finding.rule_id} {finding.path}:{finding.line} {finding.redacted_match} fingerprint={finding.fingerprint}")
    # Default to report-only mode: heuristic matches need triage and should not
    # become an unreviewed repository-wide CI baseline. Opt into a hard gate.
    return 1 if args.fail_on_findings and (findings or errors) else 0

if __name__ == "__main__":
    raise SystemExit(main())
