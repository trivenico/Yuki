"""Desktop memory composition with encrypted, account-scoped storage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companion.memory_consent import CompanionMemory
from companion.secure_memory import EncryptedMemoryStore
from core.portable_paths import desktop_data_root
from core.profile_identity import load_or_create_profile_id, account_memory_keyring_account, account_memory_filename
from ui.desktop.storage import prepare_private_desktop_root


def create_desktop_memory(keyring_module: Any, *, actor_id: str) -> CompanionMemory:
    """Create session-consent memory encrypted with a key held in the OS keyring.

    Desktop memory must never inherit the process working directory or silently
    fall back to plaintext storage.
    """
    if not callable(getattr(keyring_module, "get_keyring", None)):
        raise TypeError("A trusted OS keyring module is required.")
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
        raise ValueError("An authenticated account identity is required for desktop memory.")
    root = prepare_private_desktop_root(desktop_data_root())
    profile_id = load_or_create_profile_id(root)
    store = EncryptedMemoryStore(
        root / account_memory_filename(profile_id, actor_id),
        keyring_module=keyring_module,
        keyring_account=account_memory_keyring_account(profile_id, actor_id),
    )
    # Validate existing ciphertext and its authentication tag before exposing
    # the memory facade; never defer corruption discovery until a chat request.
    store.list()
    return CompanionMemory(store)
