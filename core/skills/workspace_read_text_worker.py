"""Standalone bounded worker for reading a small, non-sensitive workspace text file.

Input: {"arguments":{"path":"README.md","max_bytes":8192}}
Output: {"ok":true,"result":{"output":{"path":"README.md","text":"...","truncated":false}}}
This is process separation, not a complete OS sandbox. The policy intentionally rejects
hidden paths, secret-like filenames, common credential/database formats, and binary data.
"""
from __future__ import annotations

import json
from pathlib import Path
import stat
import sys


MAX_REQUEST_BYTES = 4096
MAX_FILE_BYTES = 16384
DENIED_NAMES = {
    ".env", ".env.local", ".env.production", "credentials", "credentials.json",
    "secrets", "secrets.json", "id_rsa", "id_ed25519", "known_hosts",
}
DENIED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".kdbx"}
DENIED_PARTS = {"secret", "secrets", "credential", "credentials", "token", "tokens", "private_key"}


def _fail() -> None:
    print(json.dumps({"ok": False, "result": {"error": "request_rejected"}}, separators=(",", ":")))


def _handle(request: object, root_arg: str) -> dict:
    if not isinstance(request, dict) or set(request) != {"arguments"}:
        raise ValueError("invalid request")
    args = request["arguments"]
    if not isinstance(args, dict) or set(args) != {"path", "max_bytes"}:
        raise ValueError("invalid arguments")
    rel, max_bytes = args["path"], args["max_bytes"]
    if not isinstance(rel, str) or not rel or len(rel) > 512 or "\\" in rel or "\x00" in rel:
        raise ValueError("invalid path")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_FILE_BYTES:
        raise ValueError("invalid max_bytes")
    relative = Path(rel)
    if relative.is_absolute() or any(part in {"..", ""} for part in relative.parts):
        raise ValueError("path escapes workspace")
    if any(part.startswith(".") for part in relative.parts):
        raise ValueError("hidden paths are not accessible")
    for part in relative.parts:
        lowered = part.casefold()
        stem = Path(lowered).stem
        if lowered in DENIED_NAMES or Path(lowered).suffix in DENIED_SUFFIXES or any(
            token in stem.split(".") for token in DENIED_PARTS
        ):
            raise ValueError("sensitive file path denied")

    root = Path(root_arg).resolve(strict=True)
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        try:
            if stat.S_ISLNK(candidate.lstat().st_mode):
                raise ValueError("symlink traversal denied")
        except FileNotFoundError as exc:
            raise ValueError("path does not exist") from exc
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("file is outside workspace or not a regular file")
    if resolved.stat().st_size > max_bytes:
        raise ValueError("file exceeds requested byte limit")
    raw = resolved.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError("file exceeds requested byte limit")
    if b"\x00" in raw:
        raise ValueError("binary files are not supported")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not UTF-8 text") from exc
    return {"output": {"path": relative.as_posix(), "text": text, "truncated": False}}


def main() -> int:
    if len(sys.argv) != 2:
        _fail()
        return 2
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            _fail()
            return 2
        request = json.loads(raw.decode("utf-8"))
        result = _handle(request, sys.argv[1])
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")))
        return 0
    except (ValueError, TypeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
