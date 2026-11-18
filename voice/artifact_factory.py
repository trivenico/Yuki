"""Composition root for authenticated, encrypted voice artifact storage."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from voice.key_store import VoiceEncryptionKeyProvider
from voice.secure_artifact_store import EncryptedVoiceArtifactStore


def create_user_voice_artifact_store(
    directory: str | os.PathLike[str],
    *,
    user_scope: str,
    authorize: Callable[[], bool],
    keyring_module: Any | None = None,
    max_artifact_bytes: int = 20 * 1024 * 1024,
) -> EncryptedVoiceArtifactStore:
    """Create a per-user encrypted artifact store backed by the native OS key store.

    This function must only be called after an authenticated desktop session is
    established. The store independently rechecks authorization on every use.
    """
    provider = VoiceEncryptionKeyProvider(user_scope, keyring_module=keyring_module)
    key = provider.get_or_create_key()
    return EncryptedVoiceArtifactStore(
        Path(directory),
        key=key,
        authorize=authorize,
        max_artifact_bytes=max_artifact_bytes,
    )
