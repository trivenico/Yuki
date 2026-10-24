"""Static inventory of Python capability-execution call sites.

This tool reports candidate call sites for human review; it is not a proof of
reachability, safety, or complete runtime coverage.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import sys

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build",
}
SUSPICIOUS_NAMES = {
    "run_tool", "execute", "run", "exec", "eval", "import_module",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    callee: str
    category: str
    test_file: bool


class CallSiteVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        self.findings: list[Finding] = []

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._callee_name(node.func)
        leaf = callee.rsplit(".", 1)[-1]
        category = None
        if leaf in {"exec", "eval", "import_module"}:
            category = "dynamic_execution_or_import"
        elif leaf == "run_tool":
            category = "direct_tool_dispatch"
        elif leaf in {"execute", "run"}:
            category = "capability_execution_candidate"
        if category:
            self.findings.append(Finding(
                path=self.relative_path,
                line=node.lineno,
                callee=callee,
                category=category,
                test_file=("test" in Path(self.relative_path).name.lower()
                           or "tests/" in self.relative_path.replace("\\", "/")),
            ))
        self.generic_visit(node)

    @staticmethod
    def _callee_name(node: ast.AST) -> str:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Call):
            parts.append("<call-result>")
        else:
            parts.append("<expression>")
        return ".".join(reversed(parts))


def scan_python(root: Path) -> list[Finding]:
    root = root.resolve()
    findings = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            continue
        relative = path.relative_to(root).as_posix()
        visitor = CallSiteVisitor(relative)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return findings


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else Path.cwd()
    findings = scan_python(root)
    print(json.dumps({
        "root": str(root.resolve()),
        "finding_count": len(findings),
        "warning": "Candidate call sites only; manually validate reachability and guard coverage.",
        "findings": [asdict(item) for item in findings],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
