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

@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager is Windows-only")
def test_bridge_serializes_secret_only_to_stdin_and_validates_contract(
    monkeypatch, tmp_path: Path
) -> None:
    import base64
    import json
    from subprocess import CompletedProcess

    bridge = tmp_path / "bridge.exe"
    bridge.write_bytes(b"trusted test fixture")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return CompletedProcess(args, 0, stdout=json.dumps({
                "key_id": "wincred-v1",
                "ciphertext": base64.b64encode(b"x" * 32).decode("ascii"),
            }), stderr="")
        return CompletedProcess(args, 0, stdout=json.dumps({
            "plaintext": base64.b64encode(b"secret-token").decode("ascii"),
        }), stderr="")

    monkeypatch.setattr(
        "core.windows_credential_cipher.subprocess.run", fake_run
    )
    cipher = WindowsCredentialCipher(bridge)
    key_id, ciphertext = cipher.encrypt(
        b"secret-token", associated_data=b"owner/provider/account"
    )
    assert key_id == "wincred-v1"
    assert cipher.decrypt(
        key_id, ciphertext, associated_data=b"owner/provider/account"
    ) == b"secret-token"
    assert b"secret-token" not in calls[0][1]["input"].encode("utf-8")
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["env"] == {}
    assert calls[0][1]["stderr"] == subprocess.DEVNULL


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager is Windows-only")
def test_published_dotnet_bridge_round_trips_through_python_adapter() -> None:
    """Exercise the real .NET executable, not a mocked subprocess boundary."""
    executable = os.environ.get("YUKI_WINDOWS_CREDENTIAL_BRIDGE_EXE")
    if not executable:
        pytest.skip("published .NET bridge path is not configured")

    cipher = WindowsCredentialCipher(executable)
    plaintext = b"yuki-cross-language-contract-test"
    aad = b"[1,\\\"integration-test-owner\\\",\\\"provider\\\",\\\"account\\\",\\\"access\\\"]"
    key_id, ciphertext = cipher.encrypt(plaintext, associated_data=aad)
    assert cipher.decrypt(key_id, ciphertext, associated_data=aad) == plaintext

    with pytest.raises(OAuthCredentialError, match="failed"):
        cipher.decrypt(key_id, ciphertext, associated_data=b"different-owner")
