"""Desktop memory composition with encrypted, profile-local storage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companion.memory_consent import CompanionMemory
from companion.secure_memory import EncryptedMemoryStore
from core.portable_paths import desktop_data_root
from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account
from ui.desktop.storage import prepare_private_desktop_root


def create_desktop_memory(keyring_module: Any) -> CompanionMemory:
    """Create session-consent memory encrypted with a key held in the OS keyring.

    Desktop memory must never inherit the process working directory or silently
    fall back to plaintext storage.
    """
    if not callable(getattr(keyring_module, "get_keyring", None)):
        raise TypeError("A trusted OS keyring module is required.")
    root = prepare_private_desktop_root(desktop_data_root())
    profile_id = load_or_create_profile_id(root)
    store = EncryptedMemoryStore(
        root / "memory.enc.json",
        keyring_module=keyring_module,
        keyring_account=profile_memory_keyring_account(profile_id),
    )
    # Validate existing ciphertext and its authentication tag before exposing
    # the memory facade; never defer corruption discovery until a chat request.
    store.list()
    return CompanionMemory(store)
