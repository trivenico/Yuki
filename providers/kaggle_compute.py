"""Kaggle compute preflight and explicit notebook submission planning.

Credential discovery never returns credential values. OAuth-backed CLI sessions
can be verified with a bounded, read-only CLI request. Remote notebook submission
is only planned after explicit caller approval.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


class KaggleIntegrationError(ValueError):
    """Kaggle configuration or notebook submission plan is invalid."""


@dataclass(frozen=True)
class KaggleAuthStatus:
    configured: bool
    source: str | None
    credential_path: str | None
    warning: str | None = None


@dataclass(frozen=True)
class KaggleKernelSpec:
    kernel_id: str
    folder: str
    accelerator: str | None = None
    timeout_seconds: int = 3600
    approved: bool = False


_KERNEL_ID = re.compile(r"^[A-Za-z0-9_-]{2,64}/[A-Za-z0-9_-]{2,64}$")
_ACCELERATORS = {
    "none", "NvidiaTeslaP100", "NvidiaTeslaT4", "NvidiaTeslaT4x2",
    "NvidiaL4", "NvidiaA100", "NvidiaH100", "NvidiaRtxPro6000",
    "TpuV5E8", "TpuV6E8",
}


def discover_kaggle_auth(*, environ=None, home=None) -> KaggleAuthStatus:
    """Detect supported token files without exposing their contents.

    OAuth credentials created by `kaggle auth login` are verified separately
    with `verify_kaggle_cli_auth`, because their storage format is CLI-managed.
    """
    env = os.environ if environ is None else environ
    home_path = Path.home() if home is None else Path(home)
    if isinstance(env.get("KAGGLE_API_TOKEN"), str) and env["KAGGLE_API_TOKEN"].strip():
        return KaggleAuthStatus(True, "environment_token", None)
    token_path = home_path / ".kaggle" / "access_token"
    legacy_path = home_path / ".kaggle" / "kaggle.json"
    for path, source in ((token_path, "access_token_file"), (legacy_path, "legacy_credentials_file")):
        try:
            info = path.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            return KaggleAuthStatus(False, None, str(path), "credential_file_must_be_regular_non_symlink")
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            return KaggleAuthStatus(False, None, str(path), "credential_file_permissions_too_open")
        if info.st_size <= 0 or info.st_size > 16384:
            return KaggleAuthStatus(False, None, str(path), "credential_file_size_invalid")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return KaggleAuthStatus(False, None, str(path), "credential_file_unreadable")
        if source == "access_token_file":
            if content.strip():
                return KaggleAuthStatus(True, source, str(path))
        else:
            try:
                value = json.loads(content)
            except json.JSONDecodeError:
                return KaggleAuthStatus(False, None, str(path), "legacy_credentials_invalid")
            if isinstance(value, dict) and all(
                isinstance(value.get(key), str) and value[key].strip()
                for key in ("username", "key")
            ):
                return KaggleAuthStatus(True, source, str(path))
            return KaggleAuthStatus(False, None, str(path), "legacy_credentials_invalid")
    return KaggleAuthStatus(False, None, None, "run_kaggle_auth_login_or_configure_api_token")


def verify_kaggle_cli_auth(*, executable: str | None = None, timeout_seconds: int = 20,
                           runner=subprocess.run) -> KaggleAuthStatus:
    """Verify the active Kaggle CLI session, including OAuth, without exposing output."""
    command = executable or shutil.which("kaggle")
    if not isinstance(command, str) or not command:
        return KaggleAuthStatus(False, None, None, "kaggle_cli_not_installed")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        return KaggleAuthStatus(False, None, None, "invalid_auth_check_timeout")
    try:
        result = runner(
            [command, "kernels", "list", "--mine", "--page", "1"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return KaggleAuthStatus(False, None, None, "kaggle_auth_check_timed_out")
    except OSError:
        return KaggleAuthStatus(False, None, None, "kaggle_cli_execution_failed")
    if getattr(result, "returncode", 1) == 0:
        return KaggleAuthStatus(True, "kaggle_cli_verified", None)
    return KaggleAuthStatus(False, None, None, "kaggle_authentication_or_network_check_failed")


def build_kernel_push_argv(
    spec: KaggleKernelSpec,
    *,
    executable: str | None = None,
) -> list[str]:
    if not isinstance(spec, KaggleKernelSpec):
        raise KaggleIntegrationError("invalid_kernel_spec")
    if type(spec.approved) is not bool or not spec.approved:
        raise KaggleIntegrationError("explicit_submission_approval_required")
    if not isinstance(spec.kernel_id, str) or not _KERNEL_ID.fullmatch(spec.kernel_id):
        raise KaggleIntegrationError("invalid_kernel_id")
    if not isinstance(spec.folder, str) or not os.path.isabs(spec.folder):
        raise KaggleIntegrationError("kernel_folder_must_be_absolute")
    folder = Path(spec.folder)
    metadata_path = folder / "kernel-metadata.json"
    if not folder.is_dir() or not metadata_path.is_file() or metadata_path.is_symlink():
        raise KaggleIntegrationError("kernel_folder_or_metadata_missing")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise KaggleIntegrationError("kernel_metadata_invalid") from None
    if not isinstance(metadata, dict) or metadata.get("id") != spec.kernel_id:
        raise KaggleIntegrationError("kernel_metadata_id_mismatch")
    if spec.accelerator not in _ACCELERATORS:
        raise KaggleIntegrationError("unsupported_kaggle_accelerator")
    if type(spec.timeout_seconds) is not int or not 60 <= spec.timeout_seconds <= 43200:
        raise KaggleIntegrationError("invalid_kernel_timeout")
    command = executable or shutil.which("kaggle")
    if not isinstance(command, str) or not command:
        raise KaggleIntegrationError("kaggle_cli_not_installed")
    argv = [command, "kernels", "push", "-p", str(folder), "-t", str(spec.timeout_seconds)]
    if spec.accelerator and spec.accelerator != "none":
        argv.extend(["--accelerator", spec.accelerator])
    return argv
