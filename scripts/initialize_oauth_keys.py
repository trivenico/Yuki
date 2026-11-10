"""Explicitly initialize Yuki OAuth encryption keys in the native OS key store."""
from __future__ import annotations

import argparse

from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider, OAuthOSKeyStoreError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize Yuki OAuth encryption keys in the OS credential store."
    )
    parser.add_argument(
        "--service-name",
        default="yuki",
        help="OS credential-store service namespace (default: yuki).",
    )
    args = parser.parse_args()
    try:
        provider = OSKeyringOAuthKeyProvider(service_name=args.service_name)
        provider.initialize()
        # Do not print identifiers, key bytes, or backend secrets.
        print("Yuki OAuth key store is initialized and ready.")
        return 0
    except (OAuthOSKeyStoreError, ValueError):
        print("Yuki OAuth key initialization failed; check the OS credential store.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
