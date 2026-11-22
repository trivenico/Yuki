"""Testable desktop companion controller; deliberately independent of Qt."""
from __future__ import annotations

import uuid
from pathlib import Path

from ui.desktop.backup import create_encrypted_backup, restore_encrypted_backup
from core.portable_paths import desktop_data_root
from ui.desktop.migration import migrate_data, preview_migration

from companion.application import CompanionApplicationService
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity


class DesktopCompanionController:
    """Bind one authenticated local session to the shared companion service."""

    def __init__(
        self,
        application: CompanionApplicationService,
        identity: AuthenticatedDesktopIdentity,
        sessions: DesktopSessionManager,
        *, workspace_path: Path | None = None,
    ) -> None:
        if not isinstance(application, CompanionApplicationService):
            raise TypeError("application must be a CompanionApplicationService.")
        if not isinstance(identity, AuthenticatedDesktopIdentity):
            raise TypeError("identity must be an authenticated desktop identity.")
        if not isinstance(sessions, DesktopSessionManager):
            raise TypeError("sessions must be a DesktopSessionManager.")
        current = sessions.current_identity()
        if current != identity:
            raise PermissionError("The desktop session is no longer authenticated.")
        self.application = application
        self.identity = identity
        self.sessions = sessions
        self.workspace_path = Path(workspace_path).expanduser().resolve() if workspace_path is not None else None

    def respond(self, message: str) -> str:
        if self.sessions.current_identity() != self.identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        return self.application.respond(message).message

    def set_memory_context(self, enabled: bool) -> None:
        if self.sessions.current_identity() != self.identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        if enabled:
            self.application.memory.enable_context()
        else:
            self.application.memory.disable_context()

    def memory_context_enabled(self) -> bool:
        if self.sessions.current_identity() != self.identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        return self.application.memory.context_enabled

    def _require_live_session(self) -> str:
        current = self.sessions.current_identity()
        if current != self.identity:
            raise PermissionError("The desktop session expired. Sign in again.")
        return self.identity.actor_id

    def list_specialists(self) -> tuple[dict, ...]:
        return self.application.list_specialists(actor_id=self._require_live_session())

    def propose_agent_plan(self, goal: str):
        actor_id = self._require_live_session()
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise ValueError("Plan goal must contain 1-4000 characters.")
        return self.application.propose_agent_task(
            actor_id=actor_id,
            idempotency_key="desktop-" + uuid.uuid4().hex,
            goal=goal.strip(),
        )

    def get_agent_plan(self, plan_id: str):
        return self.application.get_agent_plan(
            actor_id=self._require_live_session(), plan_id=plan_id
        )

    def dispatch_agent_plan(self, plan_id: str):
        return self.application.dispatch_agent_plan(
            actor_id=self._require_live_session(), plan_id=plan_id
        )

    def cancel_agent_plan(self, plan_id: str):
        return self.application.cancel_agent_plan(
            actor_id=self._require_live_session(), plan_id=plan_id
        )

    def list_tasks(self, *, state: str | None = None, limit: int = 50):
        return self.application.list_tasks(
            actor_id=self._require_live_session(), state=state, limit=limit
        )

    def get_task(self, task_id: str):
        return self.application.get_task(
            actor_id=self._require_live_session(), task_id=task_id
        )

    def task_progress(self, task_id: str, *, after_sequence: int = 0, limit: int = 50):
        return self.application.task_progress(
            actor_id=self._require_live_session(), task_id=task_id,
            after_sequence=after_sequence, limit=limit,
        )

    def approve_task(self, task_id: str, *, expected_version: int):
        return self.application.approve_task(
            actor_id=self._require_live_session(), task_id=task_id,
            session_id=self.identity.session_id, expected_version=expected_version,
        )

    def cancel_task(self, task_id: str, *, expected_version: int):
        return self.application.cancel_task(
            actor_id=self._require_live_session(), task_id=task_id,
            expected_version=expected_version,
        )

    def _validate_migration_destination(self, destination: Path) -> Path:
        raw = Path(destination).expanduser()
        if raw.is_symlink():
            raise ValueError("Migration destination must not be a symbolic link.")
        target = raw.resolve()
        live_data = desktop_data_root().resolve()
        if target == live_data or target in live_data.parents or live_data in target.parents:
            raise ValueError("Migration destination must be separate from the active Yuki profile.")
        return target

    def preview_migration(self, source: Path, destination: Path):
        self._require_live_session()
        target = self._validate_migration_destination(destination)
        return preview_migration(Path(source), target)

    def migrate_data(self, source: Path, destination: Path) -> dict:
        self._require_live_session()
        target = self._validate_migration_destination(destination)
        return migrate_data(Path(source), target)

    def create_backup(
        self, destination: Path, passphrase: str, *, include_memory_key: bool = False,
    ) -> dict:
        self._require_live_session()
        additional_roots = {}
        if self.workspace_path is not None:
            if self.workspace_path.exists() or self.workspace_path.is_symlink():
                additional_roots["workspace"] = self.workspace_path
        # The account namespace is derived from the authenticated session;
        # never accept an actor ID supplied by the UI or archive.
        from core.profile_identity import account_memory_filename, load_or_create_profile_id
        profile_root = desktop_data_root()
        profile_id = load_or_create_profile_id(profile_root)
        account_memory = profile_root / account_memory_filename(profile_id, self.identity.actor_id)
        legacy_memory = profile_root / "memory.enc.json"
        if legacy_memory.exists() or legacy_memory.is_symlink():
            from ui.desktop.backup import BackupError
            raise BackupError(
                "Legacy profile-wide memory is quarantined and cannot be included in a portable backup."
            )
        memory_key_export = None
        if account_memory.exists() or account_memory.is_symlink():
            if account_memory.is_symlink() or not account_memory.is_file():
                from ui.desktop.backup import BackupError
                raise BackupError("Account memory is not a safe regular file; backup was refused.")
            if not include_memory_key:
                from ui.desktop.backup import BackupError
                raise BackupError(
                    "Account memory exists. Explicitly enable encrypted memory-key export to include it safely."
                )
            memory_key_export = self.application.memory.export_key_for_encrypted_backup(
                profile_id, actor_id=self.identity.actor_id,
            )
        return create_encrypted_backup(
            profile_root, Path(destination), passphrase,
            additional_roots=additional_roots,
            memory_key_export=memory_key_export,
            memory_actor_id=self.identity.actor_id if memory_key_export is not None else None,
        )

    def restore_backup(
        self, backup: Path, destination: Path, passphrase: str, *,
        import_memory_key: bool = False,
    ) -> dict:
        self._require_live_session()
        destination_path = Path(destination).expanduser()
        if destination_path.is_symlink():
            raise ValueError("Restore destination must not be a symbolic link.")
        target = destination_path.resolve()
        live_data = desktop_data_root().resolve()
        if target == live_data or live_data in target.parents or target in live_data.parents:
            raise ValueError("Restore destination must be separate from the active Yuki profile.")
        if self.workspace_path is not None:
            live_workspace = self.workspace_path.resolve()
            if (
                target == live_workspace
                or live_workspace in target.parents
                or target in live_workspace.parents
            ):
                raise ValueError("Restore destination must be separate from the active Yuki workspace.")
        keyring_module = None
        if import_memory_key:
            from ui.desktop.auth import load_os_keyring
            keyring_module = load_os_keyring()
        return restore_encrypted_backup(
            Path(backup), target, passphrase,
            keyring_module=keyring_module,
            import_memory_key=import_memory_key,
            memory_actor_id=self.identity.actor_id if import_memory_key else None,
        )

    def logout(self) -> bool:
        """Invalidate account memory immediately, then revoke and clean up the session."""
        errors: list[Exception] = []
        revoked = False

        # Close the account-data boundary before any potentially slow task or
        # credential cleanup. A retained facade must not remain usable during
        # the logout window.
        try:
            self.application.memory.invalidate()
        except Exception as exc:
            errors.append(exc)

        try:
            # Cancel work authorized by this exact session. The store serializes
            # this against worker claims; memory is already unavailable.
            self.application.revoke_session_tasks(session_id=self.identity.session_id)
        except Exception as exc:
            errors.append(exc)

        try:
            # Revoke credentials even if durable task cleanup failed.
            revoked = self.sessions.logout(expected_identity=self.identity)
        except Exception as exc:
            errors.append(exc)

        try:
            self.application.reset_conversation()
        except Exception as exc:
            errors.append(exc)

        if errors:
            raise RuntimeError(
                "Yuki could not complete every logout operation; verify session status."
            ) from errors[0]
        return revoked
