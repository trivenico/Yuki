"""Fail-closed preflight checks for a future private Yuki deployment.

This script does not provision infrastructure or start a server. It validates
operator-supplied deployment settings and reports blockers before deployment.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def inspect_environment(env=None, *, platform=None):
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    errors = []
    warnings = []

    mode = env.get("YUKI_DEPLOYMENT_MODE", "")
    if mode != "private":
        errors.append("YUKI_DEPLOYMENT_MODE must be explicitly set to 'private'.")

    bind_host = env.get("YUKI_BIND_HOST", "")
    if bind_host not in {"127.0.0.1", "::1", "localhost"}:
        errors.append("YUKI_BIND_HOST must be loopback-only; public binding is not allowed by this preflight.")

    auth_db = env.get("YUKI_AUTH_DB", "")
    if not auth_db.strip():
        errors.append("YUKI_AUTH_DB must point to a dedicated authentication database.")
    else:
        path = Path(auth_db).expanduser()
        if not path.is_absolute():
            errors.append("YUKI_AUTH_DB must be an absolute path.")
        elif path.exists():
            if not path.is_file():
                errors.append("YUKI_AUTH_DB must refer to a regular file.")
            elif platform != "win32":
                try:
                    mode_bits = stat.S_IMODE(path.stat().st_mode)
                    if mode_bits & 0o077:
                        errors.append("Authentication database permissions are too broad; restrict access to the service account.")
                except OSError:
                    errors.append("Authentication database permissions could not be inspected.")
            else:
                warnings.append("Windows ACLs must be reviewed separately; POSIX mode-bit checks do not validate Windows access control.")
        else:
            parent = path.parent
            if not parent.exists():
                errors.append("Authentication database parent directory must exist before deployment.")
            elif platform != "win32":
                try:
                    mode_bits = stat.S_IMODE(parent.stat().st_mode)
                    if mode_bits & 0o077:
                        errors.append("Authentication database directory permissions are too broad; restrict access to the service account.")
                except OSError:
                    errors.append("Authentication database directory permissions could not be inspected.")
            else:
                warnings.append("Windows ACLs for the authentication database directory must be reviewed separately.")

    access = env.get("YUKI_PRIVATE_ACCESS", "")
    if access not in {"wireguard", "cloudflare_access", "other_vpn"}:
        errors.append("YUKI_PRIVATE_ACCESS must identify an approved private access layer.")

    if env.get("YUKI_PUBLIC_INGRESS", "").lower() in {"1", "true", "yes", "enabled"}:
        errors.append("Public ingress is prohibited by the private deployment profile.")

    if not env.get("YUKI_SECRET_SOURCE", "").strip():
        errors.append("YUKI_SECRET_SOURCE must identify the protected runtime secret source.")
    if not env.get("YUKI_BACKUP_VERIFIED", "").lower() in {"1", "true", "yes"}:
        errors.append("YUKI_BACKUP_VERIFIED must be true only after a restore test has succeeded.")
    if not env.get("YUKI_OWNER_IDENTITY", "").strip():
        errors.append("YUKI_OWNER_IDENTITY must identify the authorized owner identity.")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def main():
    result = inspect_environment()
    for warning in result["warnings"]:
        print(f"WARNING: {warning}")
    for error in result["errors"]:
        print(f"BLOCKER: {error}")
    if result["ok"]:
        print("Preflight passed. This validates configuration only; it does not deploy or certify Yuki.")
        return 0
    print("Preflight failed closed. Resolve all blockers before deployment.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
