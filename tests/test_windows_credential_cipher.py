from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from core.oauth_credential_vault import OAuthCredentialError
from core.windows_credential_cipher import WindowsCredentialCipher


def test_non_windows_platform_is_explicitly_unavailable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("core.windows_credential_cipher.os.name", "posix")
    with pytest.raises(RuntimeError, match="unavailable"):
        WindowsCredentialCipher(tmp_path / "bridge.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager is Windows-only")
def test_bridge_requires_explicit_absolute_regular_executable(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WindowsCredentialCipher("bridge.exe")
    with pytest.raises(ValueError):
        WindowsCredentialCipher(tmp_path / "missing.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager is Windows-only")
def test_bridge_timeout_and_error_messages_are_sanitized(monkeypatch, tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.exe"
    bridge.write_bytes(b"test placeholder")
    cipher = WindowsCredentialCipher(bridge)
    monkeypatch.setattr(
        "core.windows_credential_cipher.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], 1)),
    )
    with pytest.raises(OAuthCredentialError, match="timeout"):
        cipher.encrypt(b"secret-token", associated_data=b"owner/provider/account")
