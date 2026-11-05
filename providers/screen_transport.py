"""Safe SSH request planning and frame validation for screen observation.

No SSH command is executed here. The caller must obtain explicit user consent,
install the fixed helper on the remote host, and use a pinned known_hosts file.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import shlex

from providers.screen_observation_policy import (
    ScreenObservationGrant,
    authorize_screen_observation,
)


class ScreenTransportError(ValueError):
    """Invalid or unsafe remote screen transport configuration."""


@dataclass(frozen=True)
class SSHScreenTarget:
    host: str
    username: str
    identity_file: str
    known_hosts_file: str
    port: int = 22


@dataclass(frozen=True)
class ScreenFrame:
    image_bytes: bytes
    media_type: str
    width: int
    height: int


_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_HELPER = "/usr/local/libexec/yuki-screen-capture"


def build_ssh_capture_argv(
    target: SSHScreenTarget,
    *,
    session_id: str,
    timeout_seconds: int = 15,
) -> list[str]:
    """Build a pinned-host SSH argv; never accepts an arbitrary remote command."""
    if not isinstance(target, SSHScreenTarget):
        raise ScreenTransportError("invalid_ssh_target")
    if not isinstance(target.host, str) or not _HOST.fullmatch(target.host):
        raise ScreenTransportError("invalid_ssh_host")
    if not isinstance(target.username, str) or not _USER.fullmatch(target.username):
        raise ScreenTransportError("invalid_ssh_username")
    if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
        raise ScreenTransportError("invalid_session_id")
    for name, value in (
        ("identity_file", target.identity_file),
        ("known_hosts_file", target.known_hosts_file),
    ):
        if not isinstance(value, str) or not value.startswith("/") or "\n" in value:
            raise ScreenTransportError("invalid_" + name)
    if type(target.port) is not int or not 1 <= target.port <= 65535:
        raise ScreenTransportError("invalid_ssh_port")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ScreenTransportError("invalid_timeout")

    remote_args = [_HELPER, "--session-id", session_id, "--format", "png"]
    remote_command = " ".join(shlex.quote(arg) for arg in remote_args)
    return [
        "ssh",
        "-p", str(target.port),
        "-i", target.identity_file,
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + target.known_hosts_file,
        "-o", "ConnectTimeout=" + str(timeout_seconds),
        "-o", "RequestTTY=no",
        target.username + "@" + target.host,
        remote_command,
    ]


def validate_screen_frame(
    image_bytes: bytes,
    *,
    grant: ScreenObservationGrant,
    owner_id: str,
    device_id: str,
    session_id: str,
    transport: str,
    now_epoch: int,
) -> ScreenFrame:
    """Validate a PNG frame and its authorization before handing it to vision."""
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise ScreenTransportError("empty_or_invalid_frame")
    authorize_screen_observation(
        grant,
        owner_id=owner_id,
        device_id=device_id,
        session_id=session_id,
        transport=transport,
        now_epoch=now_epoch,
        image_size_bytes=len(image_bytes),
    )
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(image_bytes) < 24:
        raise ScreenTransportError("unsupported_or_invalid_image")
    if image_bytes[12:16] != b"IHDR":
        raise ScreenTransportError("invalid_png_header")
    width = int.from_bytes(image_bytes[16:20], "big")
    height = int.from_bytes(image_bytes[20:24], "big")
    if not 1 <= width <= 16384 or not 1 <= height <= 16384:
        raise ScreenTransportError("invalid_image_dimensions")
    if width * height > 80_000_000:
        raise ScreenTransportError("image_pixel_limit_exceeded")
    return ScreenFrame(image_bytes, "image/png", width, height)
