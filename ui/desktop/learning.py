"""Desktop factory for encrypted, profile-scoped learning persistence."""
from __future__ import annotations

from typing import Any

from core.portable_paths import desktop_data_root
from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account
from training.secure_learning_store import EncryptedLearningStore
from ui.desktop.storage import prepare_private_desktop_root


def create_desktop_learning_store(keyring_module: Any) -> EncryptedLearningStore:
    """Open encrypted learning state under the authenticated desktop profile.

    The store uses a separate OS-keyring service from companion memory, while
    sharing the stable profile account identity. It never falls back to
    plaintext or the process working directory.
    """
    if not callable(getattr(keyring_module, "get_keyring", None)):
        raise TypeError("A trusted OS keyring module is required.")
    root = prepare_private_desktop_root(desktop_data_root())
    profile_id = load_or_create_profile_id(root)
    store = EncryptedLearningStore(
        root / "learning.enc.json",
        keyring_module=keyring_module,
        keyring_account=profile_memory_keyring_account(profile_id),
    )
    store.read()  # Authenticate existing state before exposing it to the runtime.
    return store


__all__ = ["create_desktop_learning_store"]
