"""Stable, non-secret identity for one private Yuki desktop profile."""
from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path


_PROFILE_ID_FILENAME = "profile_identity.json"
_MAX_PROFILE_ID_BYTES = 1024


class ProfileIdentityError(RuntimeError):
    """Raised when a profile identity cannot be read or safely created."""


def _read_profile_id(path: Path) -> str:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProfileIdentityError("Yuki profile identity is not a private regular file.")
        if info.st_size <= 0 or info.st_size > _MAX_PROFILE_ID_BYTES:
            raise ProfileIdentityError("Yuki profile identity file has an invalid size.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            chunks = []
            remaining = _MAX_PROFILE_ID_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 512))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_PROFILE_ID_BYTES:
            raise ProfileIdentityError("Yuki profile identity file exceeds its size limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("invalid profile identity schema")
        value = payload.get("profile_id")
        parsed = uuid.UUID(value) if isinstance(value, str) else None
        if parsed is None or str(parsed) != value:
            raise ValueError("invalid profile identity")
        return str(parsed)
    except ProfileIdentityError:
        raise
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProfileIdentityError("Yuki profile identity is unreadable; preserve the profile and recover it before continuing.") from exc


def load_or_create_profile_id(profile_root: str | Path) -> str:
    """Read or atomically create a stable UUID; never replace an existing invalid file."""
    root = Path(profile_root).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ProfileIdentityError("Yuki profile directory is unavailable or unsafe.")
    path = root / _PROFILE_ID_FILENAME
    try:
        return _read_profile_id(path)
    except FileNotFoundError:
        pass
    profile_id = str(uuid.uuid4())
    payload = json.dumps(
        {"schema_version": 1, "profile_id": profile_id},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    import tempfile
    descriptor, temporary_name = tempfile.mkstemp(prefix=".yuki-profile-id-", dir=str(root))
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Hard-link publication is atomic and never replaces an existing identity.
            os.link(temporary, path)
        except FileExistsError:
            return _read_profile_id(path)
        if os.name == "posix":
            directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ProfileIdentityError("Yuki could not durably create its profile identity.") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return profile_id



_OWNER_FILENAME = "profile_owner.json"


def _current_os_principal() -> str:
    """Return a stable, non-secret identifier for the current local OS account."""
    if hasattr(os, "getuid"):
        return "uid:" + str(os.getuid())
    # Windows profile directories are provisioned with per-account ACLs. The
    # canonical home path distinguishes local/domain profiles without storing
    # a display name or other directly identifying account metadata.
    try:
        home = str(Path.home().resolve(strict=True))
    except OSError as exc:
        raise ProfileIdentityError("Yuki cannot identify the current OS profile.") from exc
    if not home or home == str(Path(home).anchor):
        raise ProfileIdentityError("Yuki cannot identify the current OS profile.")
    import hashlib
    normalized = os.path.normcase(os.path.normpath(home))
    return "windows-profile:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def bind_profile_to_current_os_user(
    profile_root: str | Path, *, principal: str | None = None,
) -> None:
    """Bind a private Yuki profile to its first OS owner; reject other accounts."""
    root = Path(profile_root).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ProfileIdentityError("Yuki profile directory is unavailable or unsafe.")
    expected = principal if principal is not None else _current_os_principal()
    if not isinstance(expected, str) or not expected or len(expected) > 512:
        raise ProfileIdentityError("Yuki OS owner identity is invalid.")
    import hashlib
    digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    path = root / _OWNER_FILENAME
    payload = json.dumps(
        {"schema_version": 1, "owner_digest": digest},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    try:
        info = path.lstat()
    except FileNotFoundError:
        import tempfile
        descriptor, temporary_name = tempfile.mkstemp(prefix=".yuki-owner-", dir=str(root))
        temporary = Path(temporary_name)
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        except Exception as exc:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise ProfileIdentityError("Yuki could not durably bind its local OS owner.") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        # Read the published record below, including in the concurrent-create case.
    except OSError as exc:
        raise ProfileIdentityError("Yuki could not read its local OS owner binding.") from exc
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProfileIdentityError("Yuki OS owner binding is not a private regular file.")
        if info.st_size <= 0 or info.st_size > _MAX_PROFILE_ID_BYTES:
            raise ProfileIdentityError("Yuki OS owner binding has an invalid size.")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            raw = os.read(descriptor, _MAX_PROFILE_ID_BYTES + 1)
        finally:
            os.close(descriptor)
        record = json.loads(raw.decode("utf-8"))
        if (not isinstance(record, dict) or record.get("schema_version") != 1
                or record.get("owner_digest") != digest):
            raise ProfileIdentityError(
                "This Yuki profile belongs to a different OS account. "
                "Access is denied; do not copy or reset the owner binding."
            )
    except ProfileIdentityError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProfileIdentityError("Yuki OS owner binding is unreadable; recover it before continuing.") from exc


def profile_memory_keyring_account(profile_id: str) -> str:
    """Return a bounded, profile-scoped OS-keyring account identifier."""
    try:
        canonical = str(uuid.UUID(profile_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("profile_id must be a canonical UUID.") from exc
    if canonical != profile_id:
        raise ValueError("profile_id must be a canonical UUID.")
    return "profile:" + canonical
