"""Provision host-owned trust files for a signed Yuki portable runtime.

Run this only from an independently authenticated Yuki installation. This
command does not install or authenticate its own bootstrap package.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import shlex
import subprocess
import stat
import sys
import tempfile
from pathlib import Path

from yuki_trust import portable_host
from yuki_trust.portable_bootstrap import BootstrapError

_POLICY_NAME = "portable-policy.json"
_KEY_NAME = "release-ed25519-public.pem"
_LAUNCHER_NAME = "yuki-portable-launch.py"


def _platform_id() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if system == "windows":
        family = "windows"
        python_path = "runtime/python.exe"
    elif system == "darwin":
        family = "macos"
        python_path = "runtime/bin/python"
    elif system == "linux":
        family = "linux"
        python_path = "runtime/bin/python"
    else:
        raise BootstrapError("Host installer supports Windows, macOS, and Linux only.")
    if machine not in {"x86_64", "arm64"}:
        raise BootstrapError(f"Unsupported host architecture: {machine}")
    return f"{family}-{machine}", python_path


def _default_roots() -> tuple[Path, Path]:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "Yuki" / "Trusted", base / "Yuki" / "State"
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return data / "yuki" / "trusted", state / "yuki"


def _atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    # Publish with an exclusive same-directory hard link: never replace an
    # existing trust anchor, policy, or launcher, even under a path race.
    fd, temporary = tempfile.mkstemp(prefix=".yuki-install-", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temp_path, mode)
        os.link(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    else:
        temp_path.unlink()
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


def _prepare_private_directory(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise BootstrapError(f"{label} directory must be absolute.")
    if os.name != "nt":
        portable_host._validate_host_parent_directories(path, label=label)
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise BootstrapError(f"{label} path must be a real directory, not a symlink.")
    home = Path.home().resolve()
    resolved = path.resolve(strict=True)
    if resolved == Path(resolved.anchor) or resolved == home:
        raise BootstrapError(f"{label} directory must be a dedicated child directory, not a filesystem or home root.")
    if os.name != "nt":
        if info.st_uid != os.getuid():
            raise BootstrapError(f"{label} directory must be owned by the current user.")
        portable_host._validate_host_parent_directories(path / ".yuki-trust-check", label=label)
        path.chmod(0o700)


def _validate_public_key(source: Path) -> bytes:
    # Open without following a final-component symlink where the platform
    # supports O_NOFOLLOW, then validate the opened descriptor rather than
    # trusting a separate path stat before read_bytes().
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise BootstrapError("Public key must be a readable, non-symlink host file.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise BootstrapError("Public key must be a regular PEM file no larger than 64 KiB.")
        try:
            path_info = source.stat(follow_symlinks=False)
        except OSError as exc:
            raise BootstrapError("Public key path changed during validation.") from exc
        if (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino):
            raise BootstrapError("Public key path changed during validation.")
        chunks = []
        remaining = 65537
        while remaining:
            chunk = os.read(fd, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > 65536:
            raise BootstrapError("Public key exceeds the 64 KiB size limit.")
    finally:
        os.close(fd)
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = serialization.load_pem_public_key(payload)
    except ImportError as exc:
        raise BootstrapError("Host installation requires the cryptography package.") from exc
    except (ValueError, TypeError) as exc:
        raise BootstrapError("Public key is not a valid PEM key.") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise BootstrapError("Only Ed25519 release public keys are supported.")
    return payload


def _apply_windows_acl(path: Path) -> None:
    if os.name != "nt":
        return
    sid = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip().split(",")[-1].strip().strip('"')
    if not sid.startswith("S-1-"):
        raise BootstrapError("Unable to determine the current Windows user SID.")
    subprocess.run(
        [
            "icacls", str(path), "/inheritance:r", "/grant:r",
            f"*{sid}:(F)", f"*{sid}:(OI)(CI)F",
            "*S-1-5-18:(F)", "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(F)", "*S-1-5-32-544:(OI)(CI)F", "/T",
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )


def _write_launcher(root: Path, policy_path: Path) -> tuple[Path, Path | None]:
    launcher = root / _LAUNCHER_NAME
    source = (
        "#!/" + sys.executable + "\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from yuki_trust.portable_host import launch_with_host_policy\n"
        f"POLICY = Path({str(policy_path)!r})\n"
        "if len(sys.argv) < 2:\n"
        "    raise SystemExit('Usage: yuki-portable-launch <bundle-directory> [app-args...]')\n"
        "raise SystemExit(launch_with_host_policy(sys.argv[1], POLICY, sys.argv[2:]))\n"
    ).encode("utf-8")
    _atomic_write(launcher, source)
    if os.name != "nt":
        launcher.chmod(0o600)
        shell_launcher = root / "yuki-portable-launch"
        shell_source = (
            "#!/bin/sh\n"
            f"exec {shlex.quote(sys.executable)} {shlex.quote(str(launcher))} \"$@\"\n"
        ).encode("utf-8")
        _atomic_write(shell_launcher, shell_source, mode=0o700)
        shell_launcher.chmod(0o700)
        return shell_launcher, launcher
    # Do not generate a batch wrapper: forwarding arbitrary arguments through
    # cmd.exe's %* expansion creates avoidable command-injection ambiguity.
    return launcher, None


def install_host_trust(
    public_key_source: Path,
    install_root: Path,
    state_root: Path,
    expected_key_sha256: str,
) -> dict[str, str]:
    module_path = Path(portable_host.__file__).resolve()
    try:
        module_path.relative_to(Path(sys.prefix).resolve())
    except ValueError as exc:
        raise BootstrapError(
            "Trusted bootstrap is not installed inside the active Python environment; "
            "refusing to provision trust from a source checkout or removable bundle."
        ) from exc

    key_bytes = _validate_public_key(public_key_source)
    if not isinstance(expected_key_sha256, str) or len(expected_key_sha256) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in expected_key_sha256):
        raise BootstrapError("Expected public-key SHA-256 must be exactly 64 hexadecimal characters.")
    actual_key_sha256 = hashlib.sha256(key_bytes).hexdigest()
    if not hmac.compare_digest(actual_key_sha256, expected_key_sha256.lower()):
        raise BootstrapError("Public-key SHA-256 does not match the independently supplied trust pin.")
    expected_platform, python_path = _platform_id()
    install_root = install_root.expanduser().absolute()
    state_root = state_root.expanduser().absolute()
    if install_root == state_root or install_root in state_root.parents or state_root in install_root.parents:
        raise BootstrapError("Trust and anti-rollback state directories must be separate.")
    _prepare_private_directory(install_root, "Host install")
    _prepare_private_directory(state_root, "Anti-rollback state")
    # Lock down Windows directories before creating any trust material so
    # newly created files inherit the intended ACL from their first byte.
    if os.name == "nt":
        _apply_windows_acl(install_root)
        _apply_windows_acl(state_root)

    key_path = install_root / _KEY_NAME
    policy_path = install_root / _POLICY_NAME
    state_path = state_root / "release-state.json"
    launcher = install_root / _LAUNCHER_NAME
    command = install_root / "yuki-portable-launch" if os.name != "nt" else None
    for target in (key_path, policy_path, launcher, command):
        if target is not None and (target.exists() or target.is_symlink()):
            raise BootstrapError(f"Refusing to overwrite existing host trust file: {target}")

    policy = {
        "schema_version": 1,
        "public_key": str(key_path),
        "expected_platform": expected_platform,
        "python": python_path,
        "entrypoint": "portable_entry.py",
        "anti_rollback_state": str(state_path),
    }
    try:
        _atomic_write(key_path, key_bytes)
        _atomic_write(policy_path, (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode())
        launcher, command = _write_launcher(install_root, policy_path)
        if os.name != "nt":
            key_path.chmod(0o600)
            policy_path.chmod(0o600)
            launcher.chmod(0o700)
        result = {
            "install_root": str(install_root),
            "state_root": str(state_root),
            "policy": str(policy_path),
            "public_key": str(key_path),
            "anti_rollback_state": str(state_path),
            "launcher": str(command or launcher),
            "expected_platform": expected_platform,
        }
        return result
    except Exception:
        for target in (key_path, policy_path, launcher, command):
            if target is not None:
                try:
                    target.unlink()
                except OSError:
                    pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", required=True, type=Path, help="Public key delivered through an independently authenticated channel")
    parser.add_argument("--expected-key-sha256", required=True, help="Out-of-band SHA-256 fingerprint of the exact PEM file bytes")
    parser.add_argument("--install-root", type=Path, help="Host trust directory")
    parser.add_argument("--state-root", type=Path, help="Host anti-rollback state directory")
    args = parser.parse_args()
    default_install, default_state = _default_roots()
    try:
        result = install_host_trust(
            args.public_key,
            args.install_root or default_install,
            args.state_root or default_state,
            args.expected_key_sha256,
        )
    except (OSError, ValueError, subprocess.SubprocessError, BootstrapError) as exc:
        print(f"Yuki host installation failed closed: {exc}", file=sys.stderr)
        return 2
    for key, value in result.items():
        print(f"{key}: {value}")
    print("Bootstrap trust files provisioned. This does not install or update the trusted Python package.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
