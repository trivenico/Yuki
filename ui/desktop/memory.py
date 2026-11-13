"""Desktop memory composition with encrypted, profile-local storage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companion.memory_consent import CompanionMemory
from companion.secure_memory import EncryptedMemoryStore
from core.portable_paths import desktop_data_root


def create_desktop_memory(keyring_module: Any) -> CompanionMemory:
    """Create session-consent memory encrypted with a key held in the OS keyring.

    Desktop memory must never inherit the process working directory or silently
    fall back to plaintext storage.
    """
    if not callable(getattr(keyring_module, "get_keyring", None)):
        raise TypeError("A trusted OS keyring module is required.")
    root = desktop_data_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        # Windows ACLs are managed by the user profile; POSIX permission errors
        # are not silently treated as proof that the directory is private.
        if __import__("os").name == "posix":
            raise
    store = EncryptedMemoryStore(
        root / "memory.enc.json",
        keyring_module=keyring_module,
    )
    return CompanionMemory(store)
