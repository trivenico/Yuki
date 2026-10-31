"""Deterministic, read-only second-pass reviewer for Python source changes.

This is a defense-in-depth signal generator, not an LLM reviewer, proof of safety,
or substitute for tests, human review, dependency scanning, and sandbox execution.
"""
from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Mapping


MAX_FILES = 100
MAX_FILE_CHARS = 250_000
MAX_TOTAL_CHARS = 1_000_000
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class ReviewFinding:
    path: str
    line: int
    rule_id: str
    severity: str
    message: str
    evidence: str
    remediation: str


@dataclass(frozen=True)
class ReviewReport:
    files_scanned: int
    files_skipped: int
    findings: tuple[ReviewFinding, ...]
    eligible_for_human_review: bool
    blocking_count: int
    summary: str


def _safe_path(value: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


class _PythonReviewer(ast.NodeVisitor):
    def __init__(self, path: str, source: str, redacted_lines: set[int] | None = None):
        self.path = path
        self.lines = source.splitlines()
        self.redacted_lines = redacted_lines or set()
        self.findings: list[ReviewFinding] = []

    def add(self, node: ast.AST, rule: str, severity: str, message: str, remediation: str) -> None:
        line = getattr(node, "lineno", 1)
        evidence = (
            "Potential credential-bearing line (source value redacted)."
            if line in self.redacted_lines else
            self.lines[line - 1].strip()[:240] if 0 < line <= len(self.lines) else ""
        )
        self.findings.append(ReviewFinding(
            self.path, line, rule, severity, message, evidence, remediation
        ))

    @staticmethod
    def callee(node: ast.AST) -> str:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def visit_Call(self, node: ast.Call) -> None:
        name = self.callee(node.func)
        leaf = name.rsplit(".", 1)[-1]
        if name in {"eval", "exec"}:
            self.add(node, "PY001", "critical", "Dynamic code execution detected.",
                     "Remove dynamic evaluation; use a constrained parser or explicit operation registry.")
        elif name in {"os.system", "os.popen"}:
            self.add(node, "PY002", "high", "Shell command execution through the operating system API.",
                     "Use a fixed executable and argv with shell=False, strict allowlists, and isolation.")
        elif name in {"subprocess.run", "subprocess.call", "subprocess.Popen",
                      "subprocess.check_call", "subprocess.check_output"}:
            if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                   for k in node.keywords):
                self.add(node, "PY003", "high", "Subprocess invocation enables shell=True.",
                         "Pass an argv list and keep shell=False; validate every argument.")
        elif name in {"pickle.load", "pickle.loads", "_pickle.load", "_pickle.loads"}:
            self.add(node, "PY004", "critical", "Unsafe pickle deserialization detected.",
                     "Do not deserialize untrusted pickle data; use a safe, schema-validated format.")
        elif name in {"yaml.load", "yaml.unsafe_load"}:
            safe_loader = any(k.arg == "Loader" and isinstance(k.value, ast.Attribute)
                              and k.value.attr in {"SafeLoader", "CSafeLoader"}
                              for k in node.keywords)
            if name == "yaml.unsafe_load" or not safe_loader:
                self.add(node, "PY005", "high", "YAML loader may construct arbitrary Python objects.",
                         "Use yaml.safe_load or an explicitly safe loader.")
        elif name in {"requests.get", "requests.post", "requests.put", "requests.delete",
                      "requests.request"}:
            if any(k.arg == "verify" and isinstance(k.value, ast.Constant) and k.value.value is False
                   for k in node.keywords):
                self.add(node, "PY006", "high", "TLS certificate verification is disabled.",
                         "Keep certificate verification enabled and configure trusted CA certificates.")
        elif name in {"__import__", "importlib.import_module"}:
            self.add(node, "PY007", "medium", "Dynamic module import complicates capability auditing.",
                     "Prefer explicit imports or a fixed, reviewed module registry.")
        self.generic_visit(node)


def review_python_changes(changes: Mapping[str, str]) -> ReviewReport:
    """Review a bounded mapping of repository-relative paths to proposed file contents."""
    if not isinstance(changes, Mapping) or not changes or len(changes) > MAX_FILES:
        raise ValueError("changes must contain between 1 and 100 files")
    total = 0
    findings: list[ReviewFinding] = []
    scanned = skipped = 0
    for path, source in changes.items():
        if not _safe_path(path):
            raise ValueError("change paths must be safe repository-relative paths")
        if not isinstance(source, str):
            raise TypeError("changed file contents must be strings")
        if len(source) > MAX_FILE_CHARS:
            raise ValueError("a changed file exceeds the review size limit")
        total += len(source)
        if total > MAX_TOTAL_CHARS:
            raise ValueError("total review input exceeds the size limit")
        if not path.endswith(".py"):
            skipped += 1
            continue
        scanned += 1
        redacted_lines: set[int] = set()
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                redacted_lines.add(line)
                findings.append(ReviewFinding(
                    path, line, "SEC001", "critical",
                    "Potential hard-coded credential or private key detected.",
                    "Potential credential assignment (source value redacted).",
                    "Remove the credential, rotate it, and load secrets from a managed secret store."
                ))
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            findings.append(ReviewFinding(
                path, exc.lineno or 1, "PY000", "high",
                "Python source could not be parsed; review is incomplete.",
                (exc.msg or "syntax error")[:240],
                "Fix the syntax error and rerun the independent review."
            ))
            continue
        visitor = _PythonReviewer(path, source, redacted_lines)
        visitor.visit(tree)
        findings.extend(visitor.findings)

    findings.sort(key=lambda item: (item.path, item.line, item.rule_id))
    blocking = sum(item.severity in {"critical", "high"} for item in findings)
    return ReviewReport(
        files_scanned=scanned,
        files_skipped=skipped,
        findings=tuple(findings),
        eligible_for_human_review=(blocking == 0 and scanned > 0 and skipped == 0),
        blocking_count=blocking,
        summary=("No configured high-severity patterns detected; human review remains required."
                 if blocking == 0 and scanned > 0 and skipped == 0
                 else "Review is blocked or incomplete; resolve findings and review skipped files.")
    )


def report_to_dict(report: ReviewReport) -> dict:
    result = asdict(report)
    result["findings"] = [asdict(item) for item in report.findings]
    return result
