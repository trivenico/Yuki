"""Composition root for authenticated, encrypted voice artifact storage."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Callable

from voice.key_store import VoiceEncryptionKeyProvider
from voice.secure_artifact_store import EncryptedVoiceArtifactStore


def _prepare_private_voice_directory(directory: str | os.PathLike[str]) -> Path:
    path = Path(directory).expanduser().absolute()
    if path.exists() and path.is_symlink():
        raise RuntimeError("Voice artifact directory must not be a symlink.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError("Voice artifact directory is not a safe directory.")
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise RuntimeError("Could not secure the private voice artifact directory.") from exc
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise RuntimeError("Voice artifact directory is not owned by the current user.")
    return path


def create_user_voice_artifact_store(
    directory: str | os.PathLike[str],
    *,
    user_scope: str,
    authorize: Callable[[], bool],
    keyring_module: Any | None = None,
    max_artifact_bytes: int = 20 * 1024 * 1024,
) -> EncryptedVoiceArtifactStore:
    """Create a per-user encrypted store backed by the native OS key store.

    This function must only be called after an authenticated desktop session is
    established. The same private directory also anchors the cross-process key
    lock, preventing simultaneous first-key creation from splitting encryption
    keys between concurrent Yuki instances.
    """
    private_directory = _prepare_private_voice_directory(directory)
    provider = VoiceEncryptionKeyProvider(
        user_scope, lock_directory=private_directory, keyring_module=keyring_module,
    )
    key = provider.get_or_create_key()
    return EncryptedVoiceArtifactStore(
        private_directory,
        key=key,
        authorize=authorize,
        max_artifact_bytes=max_artifact_bytes,
    )
