#!/usr/bin/env python3
"""Defensive, non-executing Python source auditor."""
from __future__ import annotations
import argparse, ast, json, re
from dataclasses import asdict, dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"}
ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SECRET_NAME = re.compile(r"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|private[_-]?key|secret[_-]?key)", re.I)
PLACEHOLDER = re.compile(r"^(?:changeme|change_me|example|sample|dummy|placeholder|your[_ -].*|<.*>|\*+|x+|none|null)$", re.I)
SECRET_VALUE = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9_-]{24,})")

@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    path: str
    line: int
    message: str
    remediation: str

class Visitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path, self.findings = path, []

    def add(self, rule, severity, node, message, remediation):
        self.findings.append(Finding(rule, severity, self.path, getattr(node, "lineno", 1), message, remediation))

    @staticmethod
    def name(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def visit_Call(self, node):
        name, leaf = self.name(node.func), self.name(node.func).rsplit(".", 1)[-1]
        if leaf in {"eval", "exec"}:
            self.add("YSEC001", "high", node, "Dynamic code execution.", "Avoid evaluating generated or untrusted code; use typed operations.")
        elif name == "os.system":
            self.add("YSEC002", "high", node, "Shell command execution via os.system().", "Use a fixed executable and argument array with shell=False inside an isolated boundary.")
        elif name.startswith("pickle.") and leaf in {"load", "loads"}:
            self.add("YSEC003", "critical", node, "Pickle deserialization can execute attacker-controlled code.", "Do not deserialize untrusted pickle; use a data-only format with schema validation.")
        elif name == "tempfile.mktemp":
            self.add("YSEC004", "high", node, "Insecure temporary path creation.", "Use NamedTemporaryFile or mkstemp and retain the secure file descriptor.")
        elif name in {"hashlib.md5", "hashlib.sha1"}:
            self.add("YSEC005", "medium", node, "Weak legacy hash algorithm.", "Use SHA-256 or stronger for integrity; use a password KDF for passwords.")
        elif name in {"yaml.load", "yaml.unsafe_load"}:
            safe = any(k.arg == "Loader" and self.name(k.value).endswith("SafeLoader") for k in node.keywords)
            if name == "yaml.unsafe_load" or not safe:
                self.add("YSEC006", "high", node, "Potentially unsafe YAML object deserialization.", "Use yaml.safe_load() or an explicitly safe loader.")
        elif name.startswith(("requests.", "httpx.")) and leaf in {"request", "get", "post", "put", "patch", "delete"}:
            if any(k.arg == "verify" and isinstance(k.value, ast.Constant) and k.value.value is False for k in node.keywords):
                self.add("YSEC007", "high", node, "TLS certificate verification is disabled.", "Keep certificate verification enabled and configure a trusted CA bundle.")
        elif name.startswith("subprocess.") and any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
            self.add("YSEC008", "high", node, "Subprocess invoked with shell=True.", "Pass an argument list with shell=False and validate executable and arguments.")
        self.generic_visit(node)

    def check_secret(self, names, value, node):
        if not names or not any(SECRET_NAME.search(n) for n in names):
            return
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return
        secret = value.value.strip()
        if len(secret) >= 12 and not PLACEHOLDER.match(secret) and (SECRET_VALUE.search(secret) or len(secret) >= 24):
            self.add("YSEC009", "high", node, "Possible hard-coded credential assigned to " + names[0] + ".", "Remove and rotate real credentials; inject secrets from a secret manager.")

    def visit_Assign(self, node):
        self.check_secret([t.id for t in node.targets if isinstance(t, ast.Name)], node.value, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        names = [node.target.id] if isinstance(node.target, ast.Name) else []
        if node.value is not None:
            self.check_secret(names, node.value, node)
        self.generic_visit(node)

def scan_python(root):
    root = Path(root).resolve()
    findings, errors = [], []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"path": path.relative_to(root).as_posix(), "error": type(exc).__name__})
            continue
        visitor = Visitor(path.relative_to(root).as_posix())
        visitor.visit(tree)
        findings.extend(visitor.findings)
    findings.sort(key=lambda f: (-ORDER[f.severity], f.path, f.line, f.rule_id))
    return findings, errors

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--fail-on", choices=tuple(ORDER), default="critical")
    args = parser.parse_args(argv)
    findings, errors = scan_python(args.root)
    result = {"schema_version": 1, "scanner": "yuki-defensive-python-auditor", "root": str(args.root.resolve()),
              "finding_count": len(findings), "parse_error_count": len(errors),
              "limitations": ["Static heuristics require human triage.", "No code executes and no network targets are contacted.", "Not a complete SAST, dependency, secret, or runtime assessment."],
              "findings": [asdict(f) for f in findings], "parse_errors": errors}
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print("Yuki defensive Python audit: " + str(len(findings)) + " finding(s), " + str(len(errors)) + " parse error(s)")
        for f in findings:
            print(f"{f.severity.upper():8} {f.rule_id} {f.path}:{f.line} {f.message}")
            print("         Fix: " + f.remediation)
        for e in errors:
            print("PARSE    " + e["path"] + ": " + e["error"])
    threshold = ORDER[args.fail_on]
    return 1 if any(ORDER[f.severity] >= threshold for f in findings) or errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
