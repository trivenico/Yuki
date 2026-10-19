"""Standalone bounded worker for read-only workspace directory listing.

Input: {"arguments":{"path":".","limit":25}}
Output: {"ok":true,"result":{"output":{"path":".","entries":[],"truncated":false}}}
No file contents are read. This worker is process-separated but not an OS sandbox.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys


MAX_REQUEST_BYTES = 4096
MAX_ENTRIES = 100


def _fail() -> None:
    print(json.dumps({"ok": False, "result": {"error": "request_rejected"}}, separators=(",", ":")))


def _handle(request: object, root_arg: str) -> dict:
    if not isinstance(request, dict) or set(request) != {"arguments"}:
        raise ValueError("invalid request")
    args = request["arguments"]
    if not isinstance(args, dict) or set(args) != {"path", "limit"}:
        raise ValueError("invalid arguments")
    rel = args["path"]
    limit = args["limit"]
    if not isinstance(rel, str) or not rel or len(rel) > 512 or "\\" in rel or "\x00" in rel:
        raise ValueError("invalid path")
    if type(limit) is not int or limit not in {10, 25, 50, 100}:
        raise ValueError("invalid limit")
    relative = Path(rel)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise ValueError("path escapes workspace")
    if any(part.startswith(".") and part not in {".", ".."} for part in relative.parts):
        raise ValueError("hidden paths are not accessible")
    root = Path(root_arg).resolve(strict=True)
    candidate = root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        candidate = candidate / part
        try:
            if stat.S_ISLNK(candidate.lstat().st_mode):
                raise ValueError("symlink traversal denied")
        except FileNotFoundError as exc:
            raise ValueError("path does not exist") from exc
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ValueError("directory is outside workspace or not a directory")

    entries = []
    for item in sorted(resolved.iterdir(), key=lambda p: p.name.casefold()):
        if item.name.startswith("."):
            continue
        try:
            mode = item.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISLNK(mode):
            kind = "symlink"
        elif stat.S_ISDIR(mode):
            kind = "directory"
        elif stat.S_ISREG(mode):
            kind = "file"
        else:
            kind = "other"
        entries.append({"name": item.name[:255], "type": kind})
        if len(entries) >= limit:
            break
    total_visible = sum(1 for item in resolved.iterdir() if not item.name.startswith("."))
    return {
        "output": {
            "path": "." if str(relative) in {"", "."} else relative.as_posix(),
            "entries": entries,
            "truncated": total_visible > len(entries),
        }
    }


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
