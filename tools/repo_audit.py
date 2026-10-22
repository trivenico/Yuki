"""Static repository audit for Python syntax and high-risk API patterns.

This tool parses source without importing or executing project modules. Findings are
advisory unless syntax errors are present; strict mode can fail on selected patterns.
"""
from __future__ import annotations
import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist", ".tox"}

@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    severity: str
    rule: str
    message: str

class AuditVisitor(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path = path
        self.findings: list[Finding] = []
    def add(self, node, severity, rule, message):
        self.findings.append(Finding(self.path, getattr(node, "lineno", 1), severity, rule, message))
    def visit_Call(self, node):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
        if name in {"eval", "exec"}:
            self.add(node, "high", "dynamic-code-execution", f"Call to {name} requires explicit security review.")
        if name in {"loads", "load"} and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "pickle":
            self.add(node, "high", "pickle-deserialization", "Pickle deserialization can execute attacker-controlled code.")
        if name in {"run", "Popen", "call", "check_call", "check_output"} and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            if any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True for keyword in node.keywords):
                self.add(node, "high", "subprocess-shell-true", "shell=True increases command-injection risk; validate necessity and inputs.")
            if name in {"run", "call", "check_call", "check_output"} and not any(keyword.arg == "timeout" for keyword in node.keywords):
                self.add(node, "medium", "subprocess-no-timeout", "Subprocess call has no explicit timeout.")
        if name in {"urlopen", "get", "post", "put", "delete", "request"}:
            module = ""
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name): module = func.value.id
            if module in {"requests", "urllib"} and not any(keyword.arg == "timeout" for keyword in node.keywords):
                self.add(node, "medium", "network-no-timeout", "Network call may block without an explicit timeout.")
        self.generic_visit(node)

def discover_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        try: relative = path.relative_to(root)
        except ValueError: continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in relative.parts[:-1]): continue
        yield path

def audit(root: Path):
    findings: list[Finding] = []
    syntax_errors = 0
    for path in discover_python_files(root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(Finding(path.relative_to(root).as_posix(), 1, "high", "source-read-error", type(exc).__name__))
            syntax_errors += 1
            continue
        except SyntaxError as exc:
            findings.append(Finding(path.relative_to(root).as_posix(), exc.lineno or 1, "high", "python-syntax-error", exc.msg))
            syntax_errors += 1
            continue
        visitor = AuditVisitor(path.relative_to(root).as_posix())
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return {"root": str(root.resolve()), "python_files_scanned": sum(1 for _ in discover_python_files(root)),
            "syntax_error_count": syntax_errors, "finding_count": len(findings),
            "findings": [asdict(item) for item in findings]}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="repository root (default: current directory)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--strict-high", action="store_true", help="exit non-zero for high-severity findings too")
    args = parser.parse_args(argv)
    report = audit(Path(args.root))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Scanned {report['python_files_scanned']} Python files; syntax errors: {report['syntax_error_count']}; findings: {report['finding_count']}")
        for item in report["findings"]:
            print(f"{item['severity'].upper()} {item['path']}:{item['line']} [{item['rule']}] {item['message']}")
    has_high = any(item["severity"] == "high" for item in report["findings"])
    return 1 if report["syntax_error_count"] or (args.strict_high and has_high) else 0

if __name__ == "__main__": raise SystemExit(main())
