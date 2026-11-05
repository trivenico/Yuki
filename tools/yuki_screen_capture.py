"""User-session Linux screenshot helper for Yuki's restricted SSH transport.

Install as /usr/local/libexec/yuki-screen-capture, owned by root and not
writable by the desktop user. Run as the logged-in desktop user, never root.
The user must create a private, short-lived grant file in XDG_RUNTIME_DIR.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_SECONDS = 12
SESSION_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CaptureError(RuntimeError):
    pass


def _validate_session_id(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise CaptureError("invalid_session_id")
    if any(char not in SESSION_ID_CHARS for char in value):
        raise CaptureError("invalid_session_id")
    return value


def _read_local_grant(session_id: str, *, now_epoch: int, runtime_dir: str | None = None) -> dict:
    if os.geteuid() == 0:
        raise CaptureError("must_not_run_as_root")
    runtime = runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not os.path.isabs(runtime):
        raise CaptureError("desktop_runtime_directory_unavailable")
    root = Path(runtime)
    try:
        root_stat = root.stat()
    except OSError as exc:
        raise CaptureError("desktop_runtime_directory_unavailable") from exc
    if root_stat.st_uid != os.getuid() or not stat.S_ISDIR(root_stat.st_mode):
        raise CaptureError("unsafe_runtime_directory")
    grant_path = root / "yuki-screen-grants" / (session_id + ".json")
    try:
        info = grant_path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise CaptureError("unsafe_grant_file")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise CaptureError("grant_file_permissions_invalid")
        if info.st_size > 4096:
            raise CaptureError("grant_file_too_large")
        with grant_path.open("r", encoding="utf-8") as handle:
            grant = json.load(handle)
    except CaptureError:
        raise
    except (OSError, ValueError) as exc:
        raise CaptureError("screen_grant_unavailable") from exc
    if not isinstance(grant, dict) or grant.get("enabled") is not True:
        raise CaptureError("screen_capture_not_enabled")
    if grant.get("session_id") != session_id:
        raise CaptureError("screen_grant_session_mismatch")
    expires = grant.get("expires_at_epoch")
    if type(expires) is not int or expires <= now_epoch or expires - now_epoch > 3600:
        raise CaptureError("screen_grant_expired_or_invalid")
    return grant


def _capture_command(env: dict[str, str], executable_lookup=shutil.which) -> list[str]:
    wayland = env.get("WAYLAND_DISPLAY")
    display = env.get("DISPLAY")
    if wayland:
        grim = executable_lookup("grim")
        if grim:
            return [grim, "-"]
    if display:
        image_import = executable_lookup("import")
        if image_import:
            return [image_import, "-window", "root", "png:-"]
    raise CaptureError("no_supported_graphical_capture_backend")


def capture_png(*, session_id: str, now_epoch: int | None = None, env=None, runner=subprocess.run, runtime_dir=None, executable_lookup=shutil.which) -> bytes:
    session_id = _validate_session_id(session_id)
    environment = dict(os.environ if env is None else env)
    if now_epoch is None:
        now_epoch = int(time.time())
    _read_local_grant(session_id, now_epoch=now_epoch, runtime_dir=runtime_dir)
    command = _capture_command(environment, executable_lookup=executable_lookup)
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=MAX_CAPTURE_SECONDS,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("screen_capture_failed_or_timed_out") from exc
    output = result.stdout
    if result.returncode != 0:
        raise CaptureError("screen_capture_backend_failed")
    if not isinstance(output, bytes) or not output.startswith(PNG_SIGNATURE):
        raise CaptureError("capture_backend_returned_invalid_png")
    if len(output) > MAX_CAPTURE_BYTES:
        raise CaptureError("capture_exceeds_size_limit")
    if len(output) < 24 or output[12:16] != b"IHDR":
        raise CaptureError("capture_png_header_invalid")
    width = int.from_bytes(output[16:20], "big")
    height = int.from_bytes(output[20:24], "big")
    if not 1 <= width <= 16384 or not 1 <= height <= 16384 or width * height > 80_000_000:
        raise CaptureError("capture_dimensions_invalid")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Capture an explicitly shared Linux desktop frame.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--format", choices=("png",), default="png")
    args = parser.parse_args(argv)
    try:
        image = capture_png(session_id=args.session_id)
    except CaptureError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.buffer.write(image)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
