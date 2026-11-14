"""Host-owned launch policy for signed Yuki portable bundles.

The policy file belongs to the host installation, never to the removable
bundle. It pins the trust anchor, platform and signed runtime entry paths.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from yuki_trust.portable_bootstrap import BootstrapError, run_verified_bundle

MAX_POLICY_BYTES = 64 * 1024
_REQUIRED_FIELDS = {
    "schema_version",
    "public_key",
    "expected_platform",
    "python",
    "entrypoint",
    "anti_rollback_state",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapError("Host launch policy contains duplicate JSON keys.")
        result[key] = value
    return result


def _validate_host_parent_directories(path: Path, *, label: str) -> None:
    """Reject replaceable or symlinked parent directories on POSIX hosts."""
    if not path.is_absolute():
        raise BootstrapError(f"{label} path must be absolute.")
    if os.name == "nt":
        return
    current = path.parent
    while True:
        try:
            info = current.lstat()
        except OSError as exc:
            raise BootstrapError(f"{label} parent directory is unavailable.") from exc
        # macOS exposes /var as a system-managed alias to /private/var.
        # Permit only this exact platform alias; arbitrary symlinked parents
        # remain rejected.
        if current == Path("/var") and current.is_symlink() and current.resolve() == Path("/private/var"):
            current = current.parent
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise BootstrapError(f"{label} parent directories must be real directories, not symlinks.")
        if info.st_uid not in {0, os.getuid()}:
            raise BootstrapError(f"{label} parent directories must be owned by root or the current user.")
        mode = stat.S_IMODE(info.st_mode)
        # A sticky world-writable directory (for example /tmp) protects
        # entries from deletion by other users; ordinary writable parents do not.
        if mode & 0o022 and not (mode & stat.S_ISVTX):
            raise BootstrapError(f"{label} parent directories must not be group- or world-writable.")
        if current.parent == current:
            break
        current = current.parent


def _host_file(path: str | Path, *, bundle: Path, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise BootstrapError(f"{label} must be a regular, non-symlink host file.")
    # Validate the caller-supplied path before resolving it so symlinked
    # parent directories cannot disappear during canonicalization.
    _validate_host_parent_directories(candidate, label=label)
    resolved = candidate.resolve(strict=True)
    # Also validate the canonical path after resolution.
    _validate_host_parent_directories(resolved, label=label)
    try:
        resolved.relative_to(bundle)
    except ValueError:
        pass
    else:
        raise BootstrapError(f"{label} must be stored outside the removable bundle.")
    if os.name != "nt":
        info = resolved.stat()
        if info.st_uid not in {0, os.getuid()}:
            raise BootstrapError(f"{label} must be owned by the current host user.")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise BootstrapError(f"{label} must not be group- or world-writable.")
    return resolved


def load_host_policy(policy_path: str | Path, bundle: str | Path) -> dict[str, str]:
    """Load a small, host-owned policy and validate its trust boundary."""
    bundle_candidate = Path(bundle).expanduser()
    if bundle_candidate.is_symlink():
        raise BootstrapError("Portable bundle root must not be a symbolic link.")
    bundle_root = bundle_candidate.resolve(strict=True)
    if not bundle_root.is_dir():
        raise BootstrapError("Portable bundle must be a directory.")

    policy_file = _host_file(policy_path, bundle=bundle_root, label="Host launch policy")
    if policy_file.stat().st_size > MAX_POLICY_BYTES:
        raise BootstrapError("Host launch policy exceeds the 64 KiB limit.")
    try:
        value = json.loads(
            policy_file.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except BootstrapError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("Host launch policy is unreadable or invalid JSON.") from exc

    if not isinstance(value, dict) or set(value) != _REQUIRED_FIELDS:
        raise BootstrapError("Host launch policy has an invalid schema.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise BootstrapError("Unsupported host launch policy schema version.")

    for field in ("public_key", "expected_platform", "python", "entrypoint", "anti_rollback_state"):
        if not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 4096:
            raise BootstrapError(f"Host launch policy field {field!r} must be non-empty text.")

    public_key = Path(value["public_key"]).expanduser()
    if not public_key.is_absolute():
        raise BootstrapError("Host trust anchor path must be absolute.")
    _host_file(public_key, bundle=bundle_root, label="Host trust anchor")

    state_path = Path(value["anti_rollback_state"]).expanduser()
    if not state_path.is_absolute():
        raise BootstrapError("Anti-rollback state path must be absolute.")
    _validate_host_parent_directories(state_path, label="Anti-rollback state")
    state_resolved = state_path.parent.resolve(strict=True) / state_path.name
    try:
        state_resolved.relative_to(bundle_root)
    except ValueError:
        pass
    else:
        raise BootstrapError("Anti-rollback state must be stored outside the removable bundle.")
    if state_resolved.is_symlink():
        raise BootstrapError("Anti-rollback state file must not be a symlink.")
    if state_resolved.exists():
        _host_file(state_resolved, bundle=bundle_root, label="Anti-rollback state")

    for field in ("python", "entrypoint"):
        relative = value[field]
        if "\\" in relative:
            raise BootstrapError(f"Host policy {field} path must use canonical bundle-relative separators.")
        parts = relative.split("/")
        if relative.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise BootstrapError(f"Host policy {field} path must be canonical and bundle-relative.")

    return {
        "public_key": str(public_key.resolve(strict=True)),
        "expected_platform": value["expected_platform"],
        "python": value["python"],
        "entrypoint": value["entrypoint"],
        "anti_rollback_state": str(state_resolved),
    }


def launch_with_host_policy(
    bundle: str | Path,
    policy_path: str | Path,
    arguments: list[str] | None = None,
) -> int:
    policy = load_host_policy(policy_path, bundle)
    return run_verified_bundle(
        bundle,
        policy["public_key"],
        policy["python"],
        policy["entrypoint"],
        arguments or [],
        expected_platform=policy["expected_platform"],
        anti_rollback_state_path=policy["anti_rollback_state"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch a signed Yuki portable bundle using host-installed trust policy."
    )
    parser.add_argument("--bundle", required=True, type=Path, help="Removable bundle directory")
    parser.add_argument("--policy", required=True, type=Path, help="Host-installed launch policy JSON")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments passed to the signed entrypoint")
    args = parser.parse_args()
    forwarded = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    try:
        return launch_with_host_policy(args.bundle, args.policy, forwarded)
    except (OSError, ValueError, BootstrapError) as exc:
        print(f"Yuki host launcher refused to launch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
