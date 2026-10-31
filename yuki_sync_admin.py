"""Local administrative CLI for issuing one-time Yuki Sync pairing codes.

Run only on the trusted Sync Hub host. This command does not start a network
listener and never exposes pairing-code creation as an unauthenticated API.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from core.portable_paths import data_root
from core.sync_auth import SyncCredentialStore
from core.sync_store import SyncEventStore


def issue_pairing_code(database: str | Path, user_id: str, ttl_seconds: int = 300) -> str:
    """Create a high-entropy single-use code using the local SQLite credential store."""
    path = Path(database).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure newly created DB files are private on POSIX systems.
    previous_umask = os.umask(0o077)
    try:
        store = SyncEventStore(path)
    finally:
        os.umask(previous_umask)
    try:
        credentials = SyncCredentialStore(store)
        return credentials.create_pairing_code(user_id, ttl_seconds)
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Issue a one-time Yuki device pairing code on the trusted hub host."
    )
    parser.add_argument("--user", required=True, help="Yuki account/user identifier")
    parser.add_argument(
        "--ttl", type=int, default=300, choices=range(30, 601),
        metavar="SECONDS", help="Code lifetime (30–600 seconds; default: 300)",
    )
    parser.add_argument(
        "--database", type=Path,
        default=data_root() / "sync" / "sync.sqlite3",
        help="Hub SQLite database path (default: Yuki user data directory)",
    )
    args = parser.parse_args(argv)
    try:
        code = issue_pairing_code(args.database, args.user, args.ttl)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Could not issue pairing code ({type(exc).__name__}).", file=sys.stderr)
        return 2
    print("One-time Yuki pairing code (deliver through a trusted private channel):")
    print(code)
    print(f"Expires in {args.ttl} seconds. It can be used only once.")
    print("Do not paste this code into logs, tickets, source control or public chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
