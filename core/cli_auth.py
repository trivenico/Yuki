"""Interactive local CLI authentication helpers for Yuki.

This module deliberately does not print, persist, or log session tokens. Callers
must gate protected command dispatch on the returned session and revoke it on exit.
"""
from __future__ import annotations

from getpass import getpass
import os
from pathlib import Path
from typing import Callable

from core.auth import AuthenticationError, BuiltInAuthService, IssuedSession

def _default_auth_db() -> Path:
    """Use a per-user state directory instead of storing credentials in the repo."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Yuki" / "auth.sqlite3"
        return Path.home() / "AppData" / "Local" / "Yuki" / "auth.sqlite3"
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "yuki" / "auth.sqlite3"


DEFAULT_AUTH_DB = _default_auth_db()


def authenticate_local_cli(
    auth_service: BuiltInAuthService,
    *,
    input_fn: Callable[[str], str] = input,
    password_fn: Callable[[str], str] = getpass,
    max_attempts: int = 3,
) -> IssuedSession | None:
    """Bootstrap the first admin or authenticate an existing account.

    Returns None after cancellation/exhausted attempts. No default credentials
    are generated; passwords are collected with getpass in interactive use.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10")

    users = auth_service.list_users()
    if not users:
        print("Yuki first-run setup: create the local administrator account.")
        try:
            username = input_fn("Admin username: ").strip()
            password = password_fn("Admin password (12+ characters): ")
            confirmation = password_fn("Confirm admin password: ")
            if password != confirmation:
                print("Setup cancelled: passwords did not match.")
                return None
            auth_service.bootstrap_admin(username, password)
        except (EOFError, KeyboardInterrupt):
            print("\nSetup cancelled.")
            return None
        except (ValueError, PermissionError):
            print("Setup failed. Check the username/password requirements and try again.")
            return None

    for attempt in range(max_attempts):
        try:
            username = input_fn("Yuki username: ").strip()
            password = password_fn("Yuki password: ")
        except (EOFError, KeyboardInterrupt):
            print("\nLogin cancelled.")
            return None
        try:
            session = auth_service.authenticate(username, password)
        except AuthenticationError:
            remaining = max_attempts - attempt - 1
            if remaining:
                print(f"Login failed. {remaining} attempt(s) remaining.")
            else:
                print("Login failed.")
            continue
        print(f"Authenticated as {session.user.username}.")
        return session
    return None
