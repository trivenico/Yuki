"""OS-keyring-backed desktop session management.

Only opaque session tokens are stored. Passwords are passed directly to the
existing BuiltInAuthService and are never persisted by this module.
"""
from __future__ import annotations

from typing import Any
from pathlib import Path

from core.json_file_store import file_lock

from core.auth import AuthenticationError, BuiltInAuthService
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity, DesktopStartupError

_KEYRING_MODULE_PREFIXES = (
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.libsecret",
)


def load_os_keyring() -> Any:
    """Return a recognized OS credential backend or fail closed."""
    try:
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        priority = getattr(backend, "priority", 0)
    except Exception as exc:
        raise DesktopStartupError(
            "Yuki Desktop requires an available operating-system credential store."
        ) from exc
    if not isinstance(priority, (int, float)) or priority <= 0 or not module.startswith(_KEYRING_MODULE_PREFIXES):
        raise DesktopStartupError(
            "No supported secure operating-system credential store is available. "
            "Configure Windows Credential Locker, macOS Keychain, or a Linux Secret Service."
        )
    return keyring


class DesktopSessionManager:
    """Authenticate against Yuki's existing account database and protect tokens in OS keyring."""

    def __init__(
        self,
        auth_service: BuiltInAuthService,
        keyring_backend: Any,
        *,
        service_name: str = "yuki.desktop",
        account_name: str = "active-session",
        lock_path: str | Path | None = None,
    ) -> None:
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService.")
        if not callable(getattr(keyring_backend, "get_password", None)) or not callable(
            getattr(keyring_backend, "set_password", None)
        ) or not callable(getattr(keyring_backend, "delete_password", None)):
            raise TypeError("keyring_backend must implement get_password, set_password, and delete_password.")
        self.auth_service = auth_service
        self.keyring = keyring_backend
        self.service_name = service_name
        self.account_name = account_name
        # The credential slot is shared by every Yuki profile/process for this
        # OS user, so its lock must not live inside a profile-specific directory.
        self.lock_path = (
            Path(lock_path).expanduser()
            if lock_path is not None
            else Path.home() / ".yuki" / "desktop-session"
        )
        self._last_identity: AuthenticatedDesktopIdentity | None = None

    def login(self, username: str, password: str) -> AuthenticatedDesktopIdentity:
        if not isinstance(username, str) or not isinstance(password, str):
            raise AuthenticationError("Invalid username or password.")
        issued = self.auth_service.authenticate(username.strip(), password)
        identity = AuthenticatedDesktopIdentity(issued.user.user_id, issued.token)
        try:
            with file_lock(self.lock_path):
                self.keyring.set_password(self.service_name, self.account_name, issued.token)
                verified = self.keyring.get_password(self.service_name, self.account_name)
                if not isinstance(verified, str) or verified != issued.token:
                    raise RuntimeError("Credential store did not retain the session.")
                # Publish the owner in the same critical section as the shared
                # credential slot to preserve ordering across concurrent logins.
                self._last_identity = identity
        except Exception as exc:
            cleanup_error: Exception | None = None
            try:
                # Serialize recovery with all Yuki credential-slot writers.
                # Never delete a different value: it may be a newer login.
                with file_lock(self.lock_path):
                    stored = self.keyring.get_password(self.service_name, self.account_name)
                    if stored == issued.token:
                        self.keyring.delete_password(self.service_name, self.account_name)
            except Exception as cleanup_exc:
                cleanup_error = cleanup_exc
            revoke_error: Exception | None = None
            try:
                self.auth_service.revoke_session(issued.token)
            except Exception as revoke_exc:
                revoke_error = revoke_exc
            if cleanup_error is not None or revoke_error is not None:
                raise DesktopStartupError(
                    "Yuki could not securely store the desktop session. Sign-in was cancelled; "
                    "credential cleanup or session revocation could not be confirmed."
                ) from (cleanup_error or revoke_error)
            raise DesktopStartupError(
                "Yuki could not securely store the desktop session. Sign-in was cancelled."
            ) from exc
        return identity

    def current_identity(self) -> AuthenticatedDesktopIdentity | None:
        try:
            with file_lock(self.lock_path):
                token = self.keyring.get_password(self.service_name, self.account_name)
                user = self.auth_service.resolve_session(token)
                if user is None:
                    self._last_identity = None
                    return None
                identity = AuthenticatedDesktopIdentity(user.user_id, token)
                self._last_identity = identity
                return identity
        except Exception as exc:
            raise DesktopStartupError("Yuki could not safely read the desktop session.") from exc

    def logout(self, *, expected_identity: AuthenticatedDesktopIdentity | None = None) -> bool:
        """Revoke only the token this controller owns; serialize keyring mutations."""
        try:
            with file_lock(self.lock_path):
                token = self.keyring.get_password(self.service_name, self.account_name)
                owner = expected_identity or self._last_identity
                if not token:
                    self._last_identity = None
                    return False
                if owner is None or token != owner.session_id:
                    # A newer login (possibly from another Yuki process) may
                    # occupy the shared slot. Never revoke an arbitrary token.
                    if self._last_identity is None or token != self._last_identity.session_id:
                        self._last_identity = None
                    return False
                self.auth_service.revoke_session(token)
                # Treat successful removal as logout success even if a prior
                # attempt already revoked the token but failed to delete it.
                self.keyring.delete_password(self.service_name, self.account_name)
                self._last_identity = None
                return True
        except Exception as exc:
            raise DesktopStartupError("Yuki could not safely revoke the desktop session.") from exc
"