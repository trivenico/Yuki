"""Advisory Agent Skills linter adapted from Hermes Agent.

Upstream: https://github.com/NousResearch/hermes-agent/blob/a938f00f0f3cdf00fdb93d9a82a9ae447e9b857f/tools/skill_linter.py
Copyright (c) 2025 Nous Research. Licensed under MIT; see
third_party/licenses/HERMES_AGENT_LICENSE. Modified for Yuki's stricter
frontmatter parser, cross-platform CLI, and bounded local-file inspection.

Findings are advisory only. Linting never grants a skill permissions and is not
a substitute for Yuki's hard validation at install time.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from core.extension_store import _UniqueKeyLoader

ERROR = "error"
WARNING = "warning"
_DESCRIPTION_SOFT_LIMIT = 160
_BODY_SOFT_BUDGET_CHARS = 24_000
_MAX_REFERENCE_FILES = 60
_MAX_FILES_SCANNED = 5_000
_MAX_SCRIPT_BYTES = 1_000_000
_MARKETING_WORDS = (
    "powerful", "comprehensive", "seamless", "advanced", "cutting-edge",
    "state-of-the-art", "revolutionary", "robust",
)
_SHELL_UTIL_TO_TOOL = {
    "grep": "search_files", "rg": "search_files", "cat": "read_file",
    "head": "read_file", "tail": "read_file", "sed": "patch", "awk": "patch",
    "find": "search_files (target='files')", "ls": "search_files (target='files')",
}
_POSIX_PRIMITIVES = (
    "fcntl", "termios", "os.setsid", "signal.SIGKILL", "osascript",
    "/proc/", "apt-get", "systemctl",
)
_FORBIDDEN_FILES = ("README.md", "CHANGELOG.md", "install.sh", ".env", ".env.example", ".gitignore")
# Heuristic only: these patterns flag suspicious skill instructions for human review.
# They must never be treated as proof that a skill is malicious or safe.
_RISK_PATTERNS = (
    ("prompt-override", re.compile(r"\b(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system|developer)\s+instructions\b", re.I)),
    ("secret-transfer", re.compile(r"\b(?:send|upload|exfiltrate|transmit|post)\b.{0,100}\b(?:api[_ -]?keys?|credentials|secrets?|access tokens?|\.env)\b", re.I | re.S)),
    ("remote-shell-pipe", re.compile(r"\b(?:curl|wget)\b.{0,160}\|\s*(?:sudo\s+)?(?:sh|bash)\b", re.I | re.S)),
    ("destructive-root-delete", re.compile(r"\brm\s+-rf\s+/(?:\s|$)", re.I)),
    ("approval-bypass", re.compile(r"\b(?:disable|bypass|turn off|skip)\b.{0,60}\b(?:approval|permissions?|security|safety checks?|sandbox)\b", re.I | re.S)),
)


@dataclass(frozen=True)
class LintFinding:
    severity: str
    rule: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _err(rule: str, message: str) -> LintFinding:
    return LintFinding(ERROR, rule, message)


def _warn(rule: str, message: str) -> LintFinding:
    return LintFinding(WARNING, rule, message)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    content = content.removeprefix("\ufeff")
    if not content.startswith("---\n"):
        return {}, content
    frontmatter, separator, body = content[4:].partition("\n---\n")
    if not separator:
        return {}, content
    parsed = yaml.load(frontmatter, Loader=_UniqueKeyLoader)
    if not isinstance(parsed, dict):
        return {}, body
    return parsed, body


def _strip_code_blocks(body: str) -> str:
    return re.sub(r"```.*?```", "", body, flags=re.DOTALL)


def _check_frontmatter(frontmatter: dict[str, Any], skill_dir: Path | None) -> Iterator[LintFinding]:
    name = str(frontmatter.get("name", "")).strip()
    if not name:
        yield _err("missing-name", "frontmatter is missing the required 'name' field.")
    elif not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", name):
        yield _err("name-format", "name must use lowercase letters, digits, hyphens, and underscores (max 48 chars).")
    if skill_dir is not None and name and name != skill_dir.name:
        yield _warn("name-dir-mismatch", f"frontmatter name '{name}' differs from directory '{skill_dir.name}'.")
    description = str(frontmatter.get("description", "")).strip().strip("'\"")
    if not description:
        yield _err("missing-description", "frontmatter is missing the required 'description' field.")
    elif len(description) > _DESCRIPTION_SOFT_LIMIT:
        yield _warn("description-length", f"description is {len(description)} chars; keep it concise (about {_DESCRIPTION_SOFT_LIMIT} max).")
    hits = [word for word in _MARKETING_WORDS if re.search(rf"\b{re.escape(word)}\b", description.lower())]
    if hits:
        yield _warn("description-marketing", f"description contains marketing language {hits}; state the concrete capability.")
    for key in ("license", "compatibility", "metadata"):
        if key not in frontmatter:
            yield _warn("missing-metadata", f"frontmatter omits optional '{key}' metadata; consider documenting it.")
    platforms = frontmatter.get("platforms")
    if platforms:
        valid = {"linux", "macos", "windows", "darwin"}
        items = platforms if isinstance(platforms, list) else [platforms]
        invalid = [value for value in items if str(value).lower() not in valid]
        if invalid:
            yield _warn("platforms-value", f"unrecognized platform values: {invalid}.")


def _check_body(body: str, skill_dir: Path | None) -> Iterator[LintFinding]:
    if len(body) > _BODY_SOFT_BUDGET_CHARS:
        yield _warn("oversized-body", f"body is {len(body)} chars; consider moving deep reference material into separate files.")
    prose = _strip_code_blocks(body)
    for rule, pattern in _RISK_PATTERNS:
        if pattern.search(prose):
            yield _warn("suspicious-instruction", f"skill content matches heuristic '{rule}'; review the instruction manually before approval.")
    for utility, replacement in _SHELL_UTIL_TO_TOOL.items():
        if re.search(rf"`{re.escape(utility)}`", prose):
            yield _warn("shell-utility-reference", f"prose references `{utility}`; prefer the registered tool name `{replacement}`.")
    if not re.search(r"^#+\s+When to Use\b", body, re.MULTILINE | re.IGNORECASE):
        yield _warn("missing-section", "no 'When to Use' section found; add explicit trigger conditions near the top.")
    refs = len(re.findall(r"(?<![\w/])#\d{3,6}\b|\b(?:PR|issue)\s*#?\d{3,6}\b", prose))
    if refs >= 4 and refs / max(len(prose), 1) * 1000 >= 0.5:
        yield _warn("incident-log-shape", f"{refs} PR/issue references in prose; prefer reusable rules over incident history.")
    if skill_dir is None:
        return
    seen: set[str] = set()
    for match in re.finditer(r"(references|templates|assets)/[\w./-]+", body):
        relative = match.group(0)
        if relative in seen or "*" in relative or relative.endswith("/"):
            continue
        seen.add(relative)
        target = (skill_dir / relative)
        try:
            target.resolve().relative_to(skill_dir.resolve())
        except (OSError, ValueError):
            yield _warn("unsafe-reference", f"reference '{relative}' resolves outside the skill directory.")
            continue
        if not target.is_file():
            yield _warn("dangling-reference", f"body references '{relative}' but the file does not exist.")


def _check_files(frontmatter: dict[str, Any], skill_dir: Path) -> Iterator[LintFinding]:
    files_seen = 0
    scripts_dir = skill_dir / "scripts"
    offenders: dict[str, list[str]] = {}
    if not frontmatter.get("platforms") and scripts_dir.is_dir():
        for script in scripts_dir.rglob("*"):
            files_seen += 1
            if files_seen > _MAX_FILES_SCANNED:
                yield _warn("scan-limit", "directory scan reached its safety limit; some files were not checked.")
                break
            if script.is_symlink() or not script.is_file() or script.suffix.lower() not in {".py", ".sh", ".bash"}:
                continue
            try:
                if script.stat().st_size > _MAX_SCRIPT_BYTES:
                    continue
                source = script.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            hits = [primitive for primitive in _POSIX_PRIMITIVES if primitive in source]
            if hits:
                offenders[str(script.relative_to(skill_dir))[:240]] = hits
    if offenders:
        yield _warn("platforms-gating", f"scripts contain POSIX-only primitives without a platforms declaration: {offenders}.")
    for filename in _FORBIDDEN_FILES:
        if (skill_dir / filename).exists():
            yield _warn("forbidden-file", f"skill contains scaffolding/config file '{filename}'.")
    references = skill_dir / "references"
    if references.is_dir():
        count = 0
        for path in references.rglob("*.md"):
            count += 1
            if count > _MAX_FILES_SCANNED:
                yield _warn("scan-limit", "reference scan reached its safety limit.")
                break
        if count > _MAX_REFERENCE_FILES:
            yield _warn("references-sprawl", f"{count} files under references/; consolidate related topics.")


def lint_content(content: str, *, skill_dir: Path | None = None) -> list[LintFinding]:
    """Lint SKILL.md text; findings are advisory and never authorize execution."""
    try:
        frontmatter, body = _parse_frontmatter(content)
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        return [_err("invalid-frontmatter", f"frontmatter could not be parsed safely ({type(exc).__name__}).")]
    findings = list(_check_frontmatter(frontmatter, skill_dir))
    findings.extend(_check_body(body, skill_dir))
    if skill_dir is not None:
        findings.extend(_check_files(frontmatter, skill_dir))
    return findings


def lint_skill(path: str | Path) -> list[LintFinding]:
    """Lint a local SKILL.md, with bounded on-disk checks."""
    source = Path(path).expanduser()
    if source.is_symlink() or source.name.lower() != "skill.md":
        return [_err("invalid-path", "select a regular file named SKILL.md (symbolic links are not followed).")]
    try:
        if source.stat().st_size > 64 * 1024:
            return [_err("file-too-large", "SKILL.md exceeds the 64 KiB inspection limit.")]
        content = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return [_err("read-error", f"SKILL.md could not be read ({type(exc).__name__}).")]
    return lint_content(content, skill_dir=source.parent)


def findings_as_json(findings: list[LintFinding]) -> dict[str, Any]:
    counts = {severity: sum(1 for finding in findings if finding.severity == severity) for severity in (ERROR, WARNING)}
    return {"valid": counts[ERROR] == 0, "errors": counts[ERROR], "warnings": counts[WARNING],
            "findings": [finding.to_dict() for finding in findings]}
