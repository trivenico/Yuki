"""Permission-checked, read-only workspace tools for specialist agents.

The caller must supply a principal created by trusted application authentication
code, never directly from request JSON. This module intentionally has no write,
shell, process, network, or Git mutation tools.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import stat
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway

_MAX_ARGUMENT_BYTES = 16 * 1024
_MAX_FILE_BYTES = 256 * 1024
_MAX_LIST_RESULTS = 200
_MAX_SEARCH_FILES = 500
_MAX_SEARCH_RESULTS = 50
_MAX_SEARCH_BYTES = 2 * 1024 * 1024
_MAX_QUERY_CHARS = 300
_BLOCKED_PARTS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh",
    ".aws", ".azure", ".config",
})
_SECRET_NAME = re.compile(
    r"(^\.env($|\.)|secret|credential|password|token|private.?key|id_rsa|id_ed25519)",
    re.IGNORECASE,
)


class ToolAuthorizationError(PermissionError):
    """A principal is not permitted to invoke a tool."""


class ToolInputError(ValueError):
    """Tool input is malformed or exceeds configured bounds."""


@dataclass(frozen=True)
class ToolPrincipal:
    actor_id: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    data: dict[str, Any]


@dataclass(frozen=True)
class _Tool:
    permission: str
    handler: Callable[[Mapping[str, Any]], dict[str, Any]]


class AuthorizedReadOnlyTools:
    """Small explicit tool registry for bounded workspace inspection."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        audit: Callable[[dict[str, Any]], None],
        principal_is_trusted: Callable[[ToolPrincipal], bool],
        authorization_gateway: AuthorizationGateway | None = None,
        principal_for_tool_principal: Callable[[ToolPrincipal], Principal] | None = None,
        policy_revision: str | None = None,
        task_id: str | None = None,
        delegation_id: str | None = None,
        approval_id: str | None = None,
    ) -> None:
        root = Path(workspace).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError("workspace must be an existing directory.")
        if not callable(audit) or not callable(principal_is_trusted):
            raise TypeError("audit and principal_is_trusted callbacks are required.")
        if authorization_gateway is not None and not isinstance(authorization_gateway, AuthorizationGateway):
            raise TypeError("authorization_gateway must be an AuthorizationGateway")
        if (authorization_gateway is None) != (principal_for_tool_principal is None):
            raise ValueError("central gateway and trusted principal resolver must be configured together")
        if principal_for_tool_principal is not None and not callable(principal_for_tool_principal):
            raise TypeError("principal_for_tool_principal must be callable")
        self._root = root
        self._audit = audit
        self._principal_is_trusted = principal_is_trusted
        self._authorization_gateway = authorization_gateway
        self._principal_for_tool_principal = principal_for_tool_principal
        self._policy_revision = policy_revision
        self._task_id = task_id
        self._delegation_id = delegation_id
        self._approval_id = approval_id
        self._tools: dict[str, _Tool] = {
            "workspace.list": _Tool("workspace:read", self._list_files),
            "workspace.read": _Tool("workspace:read", self._read_file),
            "workspace.search": _Tool("workspace:read", self._search_text),
        }

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def execute(
        self,
        *,
        principal: ToolPrincipal,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        """Authorize and run one read-only tool; all denials fail closed."""
        if not isinstance(principal, ToolPrincipal):
            raise ToolAuthorizationError("trusted authenticated principal required.")
        if not callable(self._principal_is_trusted):
            raise ToolAuthorizationError("principal validation is unavailable.")
        try:
            trusted = self._principal_is_trusted(principal) is True
        except Exception:
            trusted = False
        if not trusted:
            self._record_audit(principal, str(tool_name), "denied", "untrusted_principal")
            raise ToolAuthorizationError("trusted authenticated principal required.")
        if not isinstance(principal.actor_id, str) or not principal.actor_id.strip() or len(principal.actor_id) > 200:
            raise ToolAuthorizationError("authenticated principal is invalid.")
        if not isinstance(principal.permissions, frozenset):
            raise ToolAuthorizationError("principal permissions are invalid.")
        tool = self._tools.get(tool_name) if isinstance(tool_name, str) else None
        if tool is None:
            self._record_audit(principal, str(tool_name)[:100], "denied", "unknown_tool")
            raise ToolAuthorizationError("tool is not registered.")
        if tool.permission not in principal.permissions:
            self._record_audit(principal, tool_name, "denied", "missing_permission")
            raise ToolAuthorizationError("tool permission is required.")
        if not isinstance(arguments, Mapping):
            raise ToolInputError("arguments must be an object.")
        try:
            encoded = json.dumps(dict(arguments), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ToolInputError("arguments must be JSON-compatible.") from exc
        if len(encoded) > _MAX_ARGUMENT_BYTES:
            raise ToolInputError("arguments exceed the size limit.")

        # Resolve the actual workspace resource before central policy evaluation.
        # The path is canonicalized for policy, then securely reopened by the
        # handler using no-follow descriptor-relative operations.
        if self._authorization_gateway is not None:
            try:
                trusted_principal = self._principal_for_tool_principal(principal)
                if not isinstance(trusted_principal, Principal) or trusted_principal.principal_id != principal.actor_id:
                    raise ToolAuthorizationError("trusted principal mapping is invalid.")
                path_arg = arguments.get("path", ".")
                canonical = self._root if path_arg == "." else self._safe_path(path_arg, allow_root=True)
                relative = self._relative(self._root, canonical) if canonical != self._root else "."
                request = AuthorizationRequest(
                    principal=trusted_principal,
                    action="workspace:read",
                    resource="workspace:" + relative,
                    risk="low",
                    task_id=self._task_id,
                    delegation_id=self._delegation_id,
                    approval_id=self._approval_id,
                    expected_policy_revision=self._policy_revision,
                )
                decision = self._authorization_gateway.authorize(request)
                if getattr(decision, "allowed", None) is not True:
                    raise ToolAuthorizationError("central workspace authorization denied.")
            except ToolAuthorizationError:
                self._record_audit(principal, tool_name, "denied", "central_authorization_denied")
                raise
            except Exception as exc:
                self._record_audit(principal, tool_name, "denied", "central_authorization_unavailable")
                raise ToolAuthorizationError("central workspace authorization is unavailable.") from exc

        # The audit preflight must succeed before the handler is invoked.
        self._record_audit(principal, tool_name, "started", "authorized")
        try:
            result = tool.handler(arguments)
        except (ToolInputError, FileNotFoundError, IsADirectoryError, PermissionError):
            self._record_audit(principal, tool_name, "rejected", "input_or_path_rejected")
            raise
        except Exception as exc:
            self._record_audit(principal, tool_name, "failed", type(exc).__name__)
            raise RuntimeError("workspace tool failed safely.") from exc
        self._record_audit(principal, tool_name, "succeeded", "read_only")
        return ToolResult(tool_name=tool_name, data=result)

    def _record_audit(self, principal: ToolPrincipal, tool_name: str, outcome: str, code: str) -> None:
        actor = principal.actor_id if isinstance(principal, ToolPrincipal) and isinstance(principal.actor_id, str) else "unknown"
        event = {"actor_id": actor[:200], "tool": tool_name[:100], "outcome": outcome, "code": code[:100]}
        try:
            self._audit(event)
        except Exception as exc:
            raise RuntimeError("tool audit is unavailable; operation denied.") from exc

    def _safe_path(self, value: Any, *, allow_root: bool = False) -> Path:
        if not isinstance(value, str) or not value.strip() or len(value) > 1000 or "\x00" in value:
            raise ToolInputError("path must be bounded non-empty relative text.")
        candidate = Path(value)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise ToolInputError("absolute paths and parent traversal are not allowed.")
        if any(part in _BLOCKED_PARTS or _SECRET_NAME.search(part) for part in candidate.parts):
            raise ToolInputError("path is excluded by workspace safety policy.")
        try:
            resolved = (self._root / candidate).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ToolInputError("path does not exist or cannot be resolved safely.") from exc
        try:
            relative = resolved.relative_to(self._root)
        except ValueError as exc:
            raise ToolInputError("path escapes the configured workspace.") from exc
        if not allow_root and relative == Path("."):
            raise ToolInputError("workspace root is not a file.")
        if any(part in _BLOCKED_PARTS or _SECRET_NAME.search(part) for part in relative.parts):
            raise ToolInputError("path is excluded by workspace safety policy.")
        return resolved

    @staticmethod
    def _relative(root: Path, path: Path) -> str:
        return path.relative_to(root).as_posix()

    def _list_files(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        path_arg = arguments.get("path", ".")
        if path_arg == ".":
            directory = self._root
        else:
            directory = self._safe_path(path_arg, allow_root=True)
        if not directory.is_dir():
            raise ToolInputError("path must identify a directory.")
        files: list[str] = []
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.lower())
            truncated = False
            for child in children:
                if child.name in _BLOCKED_PARTS or _SECRET_NAME.search(child.name) or child.is_symlink():
                    continue
                try:
                    resolved = child.resolve(strict=True)
                    relative = resolved.relative_to(self._root)
                except (OSError, ValueError):
                    continue
                if any(part in _BLOCKED_PARTS or _SECRET_NAME.search(part) for part in relative.parts):
                    continue
                if resolved.is_file() or resolved.is_dir():
                    if len(files) >= _MAX_LIST_RESULTS:
                        truncated = True
                        break
                    files.append(self._relative(self._root, resolved) + ("/" if resolved.is_dir() else ""))
        except OSError as exc:
            raise RuntimeError("workspace listing failed safely.") from exc
        return {"path": self._relative(self._root, directory) if directory != self._root else ".",
                "entries": files, "truncated": truncated}

    def _read_bytes_secure(self, value: Any, *, limit: int = _MAX_FILE_BYTES) -> tuple[Path, bytes]:
        """Open beneath the workspace with no-follow descriptor-relative traversal."""
        path = self._safe_path(value)
        parts = Path(value).parts
        if not parts or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
            raise ToolInputError("secure descriptor-relative file access is unavailable.")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
        opened: list[int] = []
        try:
            current_fd = os.open(self._root, directory_flags)
            opened.append(current_fd)
            for part in parts[:-1]:
                if part in ("", "."):
                    continue
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
                opened.append(next_fd)
                current_fd = next_fd
            file_fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=current_fd,
            )
            opened.append(file_fd)
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ToolInputError("path must identify a regular file.")
            if metadata.st_size > limit:
                raise ToolInputError("file exceeds the read size limit.")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(file_fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise ToolInputError("file exceeds the read size limit.")
            return path, data
        except (OSError, RuntimeError) as exc:
            raise ToolInputError("path changed or could not be opened safely.") from exc
        finally:
            for descriptor in reversed(opened):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _read_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        path, raw = self._read_bytes_secure(arguments.get("path"))
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolInputError("only UTF-8 text files can be read.") from exc
        return {"path": self._relative(self._root, path), "content": content, "bytes": len(raw),
                "read_only": True, "untrusted_content": True}

    def _search_text(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > _MAX_QUERY_CHARS or "\x00" in query:
            raise ToolInputError("query must be bounded non-empty text.")
        case_sensitive = arguments.get("case_sensitive", False)
        if type(case_sensitive) is not bool:
            raise ToolInputError("case_sensitive must be a boolean.")
        base_arg = arguments.get("path", ".")
        base = self._root if base_arg == "." else self._safe_path(base_arg, allow_root=True)
        if not base.is_dir():
            raise ToolInputError("search path must identify a directory.")
        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        scanned_files = scanned_bytes = 0
        truncated = False
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or any(part in _BLOCKED_PARTS or _SECRET_NAME.search(part) for part in path.relative_to(self._root).parts):
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(self._root)
                if not resolved.is_file() or resolved.is_symlink():
                    continue
                size = resolved.stat().st_size
                if size > _MAX_FILE_BYTES:
                    continue
                if scanned_files >= _MAX_SEARCH_FILES or scanned_bytes + size > _MAX_SEARCH_BYTES:
                    truncated = True
                    break
                relative_path = self._relative(self._root, path)
                _opened_path, raw_bytes = self._read_bytes_secure(relative_path)
                raw = raw_bytes.decode("utf-8")
            except (OSError, UnicodeDecodeError, ValueError, ToolInputError):
                continue
            scanned_files += 1
            scanned_bytes += len(raw_bytes)
            lines = raw.splitlines()
            for line_number, line in enumerate(lines, 1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    matches.append({"path": self._relative(self._root, resolved), "line": line_number,
                                    "snippet": line[:500]})
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        truncated = True
                        break
            if len(matches) >= _MAX_SEARCH_RESULTS:
                break
        return {"query": query, "matches": matches, "scanned_files": scanned_files,
                "scanned_bytes": scanned_bytes, "truncated": truncated, "untrusted_content": True}
