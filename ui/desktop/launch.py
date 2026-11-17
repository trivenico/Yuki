"""Native PySide6 desktop companion for Yuki.

Qt remains optional and is imported only inside run(). Provider work runs on a
single background worker; widgets never access provider clients or stores.
"""
from __future__ import annotations

import sys
from typing import Any


def _close_expired_controller(controller: Any) -> Exception | None:
    """Best-effort revoke and close a controller before returning to login.

    Authorization failures can arrive from background workers after the
    authenticated session has already expired. Attempt complete cleanup before
    returning to login; callers retain the closed controller if cleanup fails so
    the owner can retry revocation without reopening session actions.
    """
    if controller is None:
        return None
    try:
        controller.logout()
    except Exception as exc:
        return exc
    return None


def _close_startup_resources(resources) -> None:
    """Best-effort reverse-order cleanup for partially composed desktop startup."""
    seen: set[int] = set()
    for resource in reversed(tuple(resources)):
        if resource is None or id(resource) in seen:
            continue
        seen.add(id(resource))
        close = getattr(resource, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Preserve the startup exception; later resources still get a
                # cleanup attempt, and normal shutdown reports aggregated errors.
                continue


def _restrict_session_to_logout_retry(
    tabs: Any,
    chat_controls: tuple[Any, ...],
    logout_button: Any,
    chat_status: Any,
) -> None:
    """Leave only the retry-sign-out control usable after failed revocation."""
    for index in range(tabs.count()):
        tabs.setTabEnabled(index, index == 0)
    for control in chat_controls:
        control.setEnabled(False)
    logout_button.setText("Retry sign out")
    logout_button.setEnabled(True)
    chat_status.setEnabled(True)


def _shutdown_desktop_services(lifecycle: Any, controller: Any, sessions: Any) -> None:
    """Revoke the active owner session, then close process resources."""
    def revoke_active_session():
        if controller is not None:
            controller.logout()
        elif sessions is not None:
            sessions.logout()

    if lifecycle is not None:
        lifecycle.close(before_close=revoke_active_session)
    else:
        revoke_active_session()


def _build_services():
    from core.auth import BuiltInAuthService, task_permission_authorizer
    from core.config import load_config
    from core.portable_paths import desktop_data_root
    from providers.factory import create_provider
    from companion.conversation import ConversationSession
    from ui.desktop.memory import create_desktop_memory
    from ui.desktop.storage import prepare_private_desktop_root
    from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
    from automation.task_runtime import TaskRuntime
    from automation.workflow_store import WorkflowRunStore
    from ui.desktop.auth import DesktopSessionManager, load_os_keyring
    from ui.desktop.lifecycle import DesktopServiceLifecycle

    config = load_config()
    from pathlib import Path
    portable_storage = bool(__import__("os").environ.get("YUKI_HOME", "").strip())
    root = prepare_private_desktop_root(
        desktop_data_root(), require_existing_parent=portable_storage,
        require_separate_volume=portable_storage,
    )
    from core.sqlite_recovery import require_healthy_sqlite_stores
    from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account
    keyring = load_os_keyring()
    profile_id = load_or_create_profile_id(root)
    memory_keyring_account = profile_memory_keyring_account(profile_id)
    from core.sqlite_recovery import inspect_encrypted_memory_store
    require_healthy_sqlite_stores(
        {
            "authentication": root / "auth.sqlite3",
            "task_workflow": root / "tasks.sqlite3",
            "conversation_history": Path(config["workspace"]) / "conversations.sqlite3",
        },
        additional_checks=(inspect_encrypted_memory_store(
            root / "memory.enc.json", keyring,
            keyring_account=memory_keyring_account,
        ),),
    )
    auth = BuiltInAuthService(str(root / "auth.sqlite3"))
    sessions = DesktopSessionManager(auth, keyring)
    provider = None
    try:
        provider = create_provider(config)
        permission_check = task_permission_authorizer(
            auth,
            session_token_provider=lambda: (
                sessions.current_identity().session_id
                if sessions.current_identity() is not None else ""
            ),
        )
        task_db = str(root / "tasks.sqlite3")
        runtime = TaskRuntime(WorkflowRunStore(task_db), authorize=permission_check)
        built_in_agents = (
            SpecialistAgent("research", "Research specialist", frozenset({"research"}), "emit"),
            SpecialistAgent("coding", "Software engineering specialist", frozenset({"coding"}), "emit"),
            SpecialistAgent("testing", "Quality assurance specialist", frozenset({"testing"}), "emit"),
            SpecialistAgent("reporting", "Technical reporting specialist", frozenset({"reporting"}), "emit"),
        )
        coordinator = SpecialistAgentCoordinator(
            runtime, task_db, built_in_agents, authorize=permission_check,
        )
        lifecycle = DesktopServiceLifecycle((auth, sessions, provider, runtime, coordinator))
        return auth, sessions, provider, coordinator, runtime, lifecycle
    except Exception:
        # Provider construction is the first potentially long-lived external
        # resource. If any later service fails, release it before propagating
        # the original startup error. Auth/session adapters currently expose no
        # persistent close handle; close any future closeable resource too.
        _close_startup_resources((auth, sessions, provider))
        raise


def run() -> int:
    """Start Yuki Desktop, or run a display-independent packaged-runtime smoke test."""
    if "--smoke-test" in sys.argv[1:]:
        # Exercise bundled first-run resources and Qt without touching the
        # runner's normal profile. Frozen windowed executables have no console,
        # so persist diagnostics when CI supplies a log path.
        import os
        import traceback
        from pathlib import Path
        from tempfile import TemporaryDirectory

        log_path = os.environ.get("YUKI_SMOKE_TEST_LOG")
        def smoke_log(message):
            if log_path:
                try:
                    with Path(log_path).open("a", encoding="utf-8") as stream:
                        stream.write(message + "\n")
                except OSError:
                    pass

        smoke_log("stage: python-entry")
        try:
            with TemporaryDirectory(prefix="yuki-desktop-smoke-") as temporary_root:
                os.environ["YUKI_CONFIG"] = str(Path(temporary_root) / "config.yaml")
                from core.config import load_config

                smoke_config = load_config()
                smoke_log("stage: packaged-config-loaded")
                if smoke_config.get("name") != "Yuki":
                    raise RuntimeError("Packaged first-run configuration did not load.")
                if not (Path(temporary_root) / "config.yaml").is_file():
                    raise RuntimeError("Packaged first-run configuration was not created.")

            smoke_log("stage: before-qt-import")
            try:
                from PySide6.QtWidgets import QApplication, QLabel
            except ImportError as exc:
                raise RuntimeError(
                    "Yuki Desktop could not load PySide6 or its native Qt runtime libraries. "
                    "Install requirements-desktop.txt and the platform's Qt system dependencies."
                ) from exc
            smoke_log("stage: qt-imported")
            # Verify optional local speech packages only in frozen bundles.
            # Source-only Qt smoke jobs intentionally omit the voice extra.
            # Importing these modules does not open a microphone or download a model.
            if getattr(sys, "frozen", False):
                try:
                    import faster_whisper  # noqa: F401
                    import numpy  # noqa: F401
                    from importlib.util import find_spec
                    if find_spec("sounddevice") is None:
                        raise ImportError("sounddevice is not bundled")
                except ImportError as exc:
                    raise RuntimeError(
                        "Packaged local voice dependencies could not be imported."
                    ) from exc
                # Do not import sounddevice here: PortAudio initialization can
                # require an active host audio server, which headless CI lacks.
                smoke_log("stage: voice-dependencies-discovered")
            app = QApplication.instance() or QApplication([sys.argv[0]])
            probe = QLabel("Yuki Desktop runtime OK")
            smoke_log("stage: app-created")
            probe.show()
            app.processEvents()
            if not probe.isVisible():
                raise RuntimeError("Qt could not initialize a visible desktop widget.")
            smoke_log("stage: widget-visible")
            probe.close()
            app.processEvents()
            smoke_log("stage: success")
            return 0
        except BaseException:
            log_path = os.environ.get("YUKI_SMOKE_TEST_LOG")
            if log_path:
                try:
                    Path(log_path).write_text("stage: python-exception\n" + traceback.format_exc(), encoding="utf-8")
                except OSError:
                    pass
            raise

    try:
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
        from PySide6.QtWidgets import (
            QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
            QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
            QStackedWidget, QVBoxLayout, QWidget, QTabWidget,
            QListWidget, QListWidgetItem, QTextEdit, QFileDialog,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Yuki Desktop requires the optional dependencies. "
            "Install them with: python -m pip install -r requirements-desktop.txt"
        ) from exc

    from core.config import load_config
    from companion.conversation import ConversationSession
    from ui.desktop.memory import create_desktop_memory
    from ui.desktop.bootstrap import DesktopStartupError, build_desktop_context
    from ui.desktop.controller import DesktopCompanionController

    class WorkerSignals(QObject):
        finished = Signal(str, object)
        phase = Signal(str)

    class ChatWorker(QRunnable):
        def __init__(self, controller: DesktopCompanionController, message: str):
            super().__init__()
            self.controller = controller
            self.message = message
            self.signals = WorkerSignals()

        @Slot()
        def run(self):
            try:
                self.signals.finished.emit(self.controller.respond(self.message), None)
            except Exception as exc:
                self.signals.finished.emit("", exc)

    class VoiceWorkerSignals(QObject):
        finished = Signal(str, object, object)
        phase = Signal(str)

    class VoiceWorker(QRunnable):
        def __init__(self, session):
            super().__init__()
            self.session = session
            self.signals = VoiceWorkerSignals()

        @Slot()
        def run(self):
            try:
                transcript = self.session.capture_and_transcribe(
                    seconds=5,
                    on_capture_complete=lambda: self.signals.phase.emit("transcribing"),
                )
                self.signals.finished.emit(transcript, None, self.session)
            except Exception as exc:
                self.signals.finished.emit("", exc, self.session)


    class SpeechWorkerSignals(QObject):
        finished = Signal(object, object, object, object)

    class SpeechWorker(QRunnable):
        def __init__(self, session, reply: str):
            super().__init__()
            self.session = session
            self.reply = reply
            self.signals = SpeechWorkerSignals()

        @Slot()
        def run(self):
            try:
                result = self.session.speak_reply(
                    self.reply, voice_mode_active=True,
                )
                self.signals.finished.emit(result, None, self.session, self)
            except Exception as exc:
                self.signals.finished.emit(None, exc, self.session, self)

    class PlanWorkerSignals(QObject):
        finished = Signal(object, object)

    class PlanWorker(QRunnable):
        def __init__(self, controller: DesktopCompanionController, goal: str):
            super().__init__()
            self.controller = controller
            self.goal = goal
            self.signals = PlanWorkerSignals()

        @Slot()
        def run(self):
            try:
                self.signals.finished.emit(
                    self.controller.propose_agent_plan(self.goal), None
                )
            except Exception as exc:
                self.signals.finished.emit(None, exc)

    class BackupWorkerSignals(QObject):
        finished = Signal(object, object)

    class BackupWorker(QRunnable):
        def __init__(self, operation):
            super().__init__()
            self.operation = operation
            self.signals = BackupWorkerSignals()

        @Slot()
        def run(self):
            try:
                self.signals.finished.emit(self.operation(), None)
            except Exception as exc:
                self.signals.finished.emit(None, exc)

    class LogoutWorkerSignals(QObject):
        finished = Signal(object)

    class LogoutWorker(QRunnable):
        """Revoke the current session after preceding desktop work drains."""

        def __init__(self, controller, sessions):
            super().__init__()
            self.controller = controller
            self.sessions = sessions
            self.signals = LogoutWorkerSignals()

        @Slot()
        def run(self):
            error = None
            try:
                if self.controller is not None:
                    self.controller.logout()
                elif self.sessions is not None:
                    self.sessions.logout()
            except Exception as exc:
                error = exc
            self.signals.finished.emit(error)

    class ShutdownWorkerSignals(QObject):
        finished = Signal(object)

    class ShutdownWorker(QRunnable):
        """Run ordered service shutdown away from the Qt event thread."""

        def __init__(self, lifecycle, controller, sessions):
            super().__init__()
            self.lifecycle = lifecycle
            self.controller = controller
            self.sessions = sessions
            self.signals = ShutdownWorkerSignals()

        @Slot()
        def run(self):
            error = None
            try:
                _shutdown_desktop_services(
                    self.lifecycle, self.controller, self.sessions,
                )
            except Exception as exc:
                error = exc
            self.signals.finished.emit(error)

    class YukiWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Yuki")
            self.setMinimumSize(820, 620)
            self.pool = QThreadPool(self)
            self.pool.setMaxThreadCount(1)
            self.auth = None
            self.sessions = None
            self.provider = None
            self.lifecycle = None
            self.controller = None
            self.voice_session = None
            self.speech_session = None
            self.speech_worker = None
            self.busy = False
            self._logout_started = False
            self._logout_worker = None
            self._shutdown_started = False
            self._shutdown_complete = False
            self._shutdown_worker = None
            self.current_plan = None
            self.migration_preview = None
            self._build_ui()
            self._initialize()

        def _build_ui(self):
            self.pages = QStackedWidget()
            self.setCentralWidget(self.pages)

            login = QWidget()
            login_layout = QVBoxLayout(login)
            login_layout.addWidget(QLabel("<h2>Welcome to Yuki</h2>"))
            login_layout.addWidget(QLabel("Sign in with your local Yuki account."))
            form = QFormLayout()
            self.username = QLineEdit()
            self.username.setPlaceholderText("Username")
            self.password = QLineEdit()
            self.password.setEchoMode(QLineEdit.Password)
            self.password.setPlaceholderText("Password")
            form.addRow("Username", self.username)
            form.addRow("Password", self.password)
            login_layout.addLayout(form)
            self.login_button = QPushButton("Sign in")
            self.login_button.clicked.connect(self._login)
            self.password.returnPressed.connect(self._login)
            login_layout.addWidget(self.login_button)
            self.login_status = QLabel(
                "Your password is never stored. Create the first account with "
                "'yuki auth bootstrap-admin' in the terminal."
            )
            self.login_status.setWordWrap(True)
            login_layout.addWidget(self.login_status)
            login_layout.addStretch(1)
            self.pages.addWidget(login)

            chat = QWidget()
            chat_layout = QVBoxLayout(chat)
            header = QHBoxLayout()
            self.identity_label = QLabel("Signed in")
            self.memory_toggle = QCheckBox("Use saved memory in replies")
            self.memory_toggle.setToolTip(
                "Allow saved notes to be included in this session's chat context. "
                "This does not enable automatic memory saving."
            )
            self.memory_toggle.toggled.connect(self._set_memory_context)
            self.logout_button = QPushButton("Sign out")
            self.logout_button.clicked.connect(self._logout)
            header.addWidget(self.identity_label)
            header.addStretch(1)
            header.addWidget(self.memory_toggle)
            header.addWidget(self.logout_button)
            chat_layout.addLayout(header)
            self.transcript = QPlainTextEdit()
            self.transcript.setReadOnly(True)
            self.transcript.setPlaceholderText("Your conversation appears here.")
            chat_layout.addWidget(self.transcript, 1)
            composer = QHBoxLayout()
            self.message = QLineEdit()
            self.message.setMaxLength(4000)
            self.message.setPlaceholderText("Message Yuki…")
            self.message.returnPressed.connect(self._send)
            self.send_button = QPushButton("Send")
            self.send_button.clicked.connect(self._send)
            composer.addWidget(self.message, 1)
            composer.addWidget(self.send_button)
            chat_layout.addLayout(composer)
            voice_controls = QHBoxLayout()
            self.voice_checkbox = QCheckBox("Allow microphone for this session")
            self.voice_checkbox.setChecked(False)
            self.voice_checkbox.toggled.connect(self._set_voice_enabled)
            self.voice_button = QPushButton("Speak (up to 5 seconds)")
            self.voice_button.setEnabled(False)
            self.voice_button.clicked.connect(self._start_voice_capture)
            self.voice_stop_button = QPushButton("Stop microphone")
            self.voice_stop_button.setEnabled(False)
            self.voice_stop_button.clicked.connect(self._stop_voice_capture)
            self.spoken_replies_checkbox = QCheckBox("Enable spoken replies")
            self.spoken_replies_checkbox.setChecked(False)
            self.spoken_replies_checkbox.toggled.connect(self._set_spoken_replies_enabled)
            self.speech_stop_button = QPushButton("Stop speaking")
            self.speech_stop_button.setEnabled(False)
            self.speech_stop_button.clicked.connect(self._stop_spoken_reply)
            voice_controls.addWidget(self.voice_checkbox)
            voice_controls.addWidget(self.voice_button)
            voice_controls.addWidget(self.voice_stop_button)
            voice_controls.addWidget(self.spoken_replies_checkbox)
            voice_controls.addWidget(self.speech_stop_button)
            chat_layout.addLayout(voice_controls)
            self.voice_status = QLabel("Microphone off. Spoken replies off. No background listening.")
            self.voice_status.setWordWrap(True)
            chat_layout.addWidget(self.voice_status)
            self.chat_status = QLabel("Ready")
            chat_layout.addWidget(self.chat_status)
            tabs = QTabWidget()
            tabs.addTab(chat, "Chat")

            specialists_page = QWidget()
            specialists_layout = QVBoxLayout(specialists_page)
            specialists_layout.addWidget(QLabel("Registered specialists and their routing capabilities"))
            self.specialist_list = QListWidget()
            specialists_layout.addWidget(self.specialist_list, 1)
            specialist_refresh = QPushButton("Refresh specialists")
            specialist_refresh.clicked.connect(self._refresh_specialists)
            specialists_layout.addWidget(specialist_refresh)
            tabs.addTab(specialists_page, "Specialists")

            plans_page = QWidget()
            plans_layout = QVBoxLayout(plans_page)
            plans_layout.addWidget(QLabel("Create a proposal. Nothing is queued until you review and dispatch it."))
            self.plan_goal = QLineEdit()
            self.plan_goal.setMaxLength(4000)
            self.plan_goal.setPlaceholderText("What should Yuki plan?")
            plans_layout.addWidget(self.plan_goal)
            self.plan_propose_button = QPushButton("Propose plan")
            self.plan_propose_button.clicked.connect(self._propose_plan)
            plans_layout.addWidget(self.plan_propose_button)
            self.plan_details = QTextEdit()
            self.plan_details.setReadOnly(True)
            self.plan_details.setPlaceholderText("A proposed plan will appear here for review.")
            plans_layout.addWidget(self.plan_details, 1)
            plan_actions = QHBoxLayout()
            self.plan_dispatch_button = QPushButton("Dispatch reviewed plan")
            self.plan_dispatch_button.setEnabled(False)
            self.plan_dispatch_button.clicked.connect(self._dispatch_plan)
            self.plan_cancel_button = QPushButton("Cancel plan")
            self.plan_cancel_button.setEnabled(False)
            self.plan_cancel_button.clicked.connect(self._cancel_plan)
            plan_actions.addWidget(self.plan_dispatch_button)
            plan_actions.addWidget(self.plan_cancel_button)
            plans_layout.addLayout(plan_actions)
            tabs.addTab(plans_page, "Plans")

            tasks_page = QWidget()
            tasks_layout = QVBoxLayout(tasks_page)
            tasks_layout.addWidget(QLabel("Durable task summaries — no worker is started from this screen."))
            self.task_list = QListWidget()
            self.task_list.currentItemChanged.connect(self._update_task_actions)
            tasks_layout.addWidget(self.task_list, 1)
            self.task_progress_view = QTextEdit()
            self.task_progress_view.setReadOnly(True)
            self.task_progress_view.setMaximumHeight(160)
            self.task_progress_view.setPlaceholderText("Select a task and view its redacted progress history.")
            tasks_layout.addWidget(self.task_progress_view)
            task_actions = QHBoxLayout()
            task_refresh = QPushButton("Refresh tasks")
            task_refresh.clicked.connect(self._refresh_tasks)
            self.task_cancel_button = QPushButton("Cancel selected task")
            self.task_cancel_button.clicked.connect(self._cancel_selected_task)
            self.task_approve_button = QPushButton("Review & approve")
            self.task_approve_button.clicked.connect(self._review_and_approve_task)
            task_progress = QPushButton("View progress")
            task_progress.clicked.connect(self._show_selected_progress)
            task_actions.addWidget(task_refresh)
            task_actions.addWidget(task_progress)
            task_actions.addWidget(self.task_approve_button)
            task_actions.addWidget(self.task_cancel_button)
            tasks_layout.addLayout(task_actions)
            tabs.addTab(tasks_page, "Tasks")

            backup_page = QWidget()
            backup_layout = QVBoxLayout(backup_page)
            backup_layout.addWidget(QLabel("Create a portable encrypted backup of your Yuki profile, including the configured workspace files."))
            backup_layout.addWidget(QLabel(
                "Choose a new backup file and a passphrase of at least 12 characters. "
                "Yuki cannot recover a lost passphrase."
            ))
            backup_layout.addWidget(QLabel(
                "Portable memory is optional. When enabled, its profile key is included only "
                "inside the passphrase-encrypted archive and imported into the destination OS keyring."
            ))
            self.backup_path = QLineEdit()
            self.backup_path.setReadOnly(True)
            backup_path_row = QHBoxLayout()
            backup_path_row.addWidget(self.backup_path, 1)
            choose_backup = QPushButton("Choose backup file…")
            choose_backup.clicked.connect(self._choose_backup_path)
            backup_path_row.addWidget(choose_backup)
            backup_layout.addLayout(backup_path_row)
            self.backup_passphrase = QLineEdit()
            self.backup_passphrase.setEchoMode(QLineEdit.Password)
            self.backup_passphrase.setPlaceholderText("Backup passphrase")
            self.backup_confirmation = QLineEdit()
            self.backup_confirmation.setEchoMode(QLineEdit.Password)
            self.backup_confirmation.setPlaceholderText("Confirm backup passphrase")
            backup_layout.addWidget(self.backup_passphrase)
            backup_layout.addWidget(self.backup_confirmation)
            self.include_memory_key = QCheckBox(
                "Include portable encrypted-memory key (recommended for cross-device restore)"
            )
            self.include_memory_key.setChecked(False)
            backup_layout.addWidget(self.include_memory_key)
            self.backup_create_button = QPushButton("Create encrypted backup")
            self.backup_create_button.clicked.connect(self._create_backup)
            backup_layout.addWidget(self.backup_create_button)
            backup_layout.addWidget(QLabel("Restore into a separate, empty folder. The active Yuki profile is never overwritten."))
            self.restore_path = QLineEdit()
            self.restore_path.setReadOnly(True)
            restore_file_row = QHBoxLayout()
            restore_file_row.addWidget(self.restore_path, 1)
            choose_restore = QPushButton("Choose backup…")
            choose_restore.clicked.connect(self._choose_restore_path)
            restore_file_row.addWidget(choose_restore)
            backup_layout.addLayout(restore_file_row)
            self.restore_destination = QLineEdit()
            self.restore_destination.setReadOnly(True)
            restore_destination_row = QHBoxLayout()
            restore_destination_row.addWidget(self.restore_destination, 1)
            choose_destination = QPushButton("Choose empty destination…")
            choose_destination.clicked.connect(self._choose_restore_destination)
            restore_destination_row.addWidget(choose_destination)
            backup_layout.addLayout(restore_destination_row)
            self.restore_passphrase = QLineEdit()
            self.restore_passphrase.setEchoMode(QLineEdit.Password)
            self.restore_passphrase.setPlaceholderText("Backup passphrase")
            backup_layout.addWidget(self.restore_passphrase)
            self.import_memory_key = QCheckBox(
                "Import portable encrypted-memory key into this computer's OS keyring"
            )
            self.import_memory_key.setChecked(True)
            backup_layout.addWidget(self.import_memory_key)
            self.backup_restore_button = QPushButton("Restore profile package into selected folder")
            self.backup_restore_button.clicked.connect(self._restore_backup)
            backup_layout.addWidget(self.backup_restore_button)
            self.backup_status = QLabel("Backup and restore are available after signing in.")
            self.backup_status.setWordWrap(True)
            backup_layout.addWidget(self.backup_status)
            backup_layout.addStretch(1)
            tabs.addTab(backup_page, "Backup & Restore")

            migration_page = QWidget()
            migration_layout = QVBoxLayout(migration_page)
            migration_layout.addWidget(QLabel(
                "Move data from an earlier Yuki installation into a separate, empty folder. "
                "The original data is retained."
            ))
            migration_layout.addWidget(QLabel(
                "Migration does not activate or replace the running profile. "
                "Create an encrypted backup before proceeding."
            ))
            self.migration_source = QLineEdit()
            self.migration_source.setReadOnly(True)
            source_row = QHBoxLayout()
            source_row.addWidget(self.migration_source, 1)
            self.migration_source_button = QPushButton("Choose old data folder…")
            self.migration_source_button.clicked.connect(self._choose_migration_source)
            source_row.addWidget(self.migration_source_button)
            migration_layout.addLayout(source_row)
            self.migration_destination = QLineEdit()
            self.migration_destination.setReadOnly(True)
            destination_row = QHBoxLayout()
            destination_row.addWidget(self.migration_destination, 1)
            self.migration_destination_button = QPushButton("Choose new empty folder…")
            self.migration_destination_button.clicked.connect(self._choose_migration_destination)
            destination_row.addWidget(self.migration_destination_button)
            migration_layout.addLayout(destination_row)
            migration_actions = QHBoxLayout()
            self.migration_preview_button = QPushButton("Preview migration")
            self.migration_preview_button.clicked.connect(self._preview_migration)
            migration_actions.addWidget(self.migration_preview_button)
            self.migration_execute_button = QPushButton("Migrate verified copy")
            self.migration_execute_button.setEnabled(False)
            self.migration_execute_button.clicked.connect(self._execute_migration)
            migration_actions.addWidget(self.migration_execute_button)
            migration_layout.addLayout(migration_actions)
            self.migration_preview_view = QPlainTextEdit()
            self.migration_preview_view.setReadOnly(True)
            self.migration_preview_view.setPlaceholderText(
                "Select source and destination, then preview the migration."
            )
            migration_layout.addWidget(self.migration_preview_view, 1)
            self.migration_status = QLabel("Migration is available after signing in.")
            self.migration_status.setWordWrap(True)
            migration_layout.addWidget(self.migration_status)
            tabs.addTab(migration_page, "Migration")
            self.authenticated_tabs = tabs

            self.pages.addWidget(tabs)

        def _clear_migration_preview(self):
            self.migration_preview = None
            self.migration_execute_button.setEnabled(False)
            self.migration_preview_view.clear()
            self.migration_status.setText("Preview required before migration.")

        def _choose_migration_source(self):
            path = QFileDialog.getExistingDirectory(self, "Choose earlier Yuki data folder")
            if path:
                self.migration_source.setText(path)
                self._clear_migration_preview()

        def _choose_migration_destination(self):
            path = QFileDialog.getExistingDirectory(self, "Choose separate empty destination")
            if path:
                self.migration_destination.setText(path)
                self._clear_migration_preview()

        def _preview_migration(self):
            if self.controller is None or self.busy:
                return
            source = self.migration_source.text().strip()
            destination = self.migration_destination.text().strip()
            if not source or not destination:
                self.migration_status.setText("Choose both a source and destination folder.")
                return
            self.busy = True
            self.migration_preview_button.setEnabled(False)
            self.migration_execute_button.setEnabled(False)
            self.migration_source_button.setEnabled(False)
            self.migration_destination_button.setEnabled(False)
            self.migration_status.setText("Inspecting source files without modifying them…")
            controller = self.controller
            worker = BackupWorker(
                lambda: controller.preview_migration(source, destination)
            )
            worker.signals.finished.connect(self._migration_preview_finished)
            self.pool.start(worker)

        @Slot(object, object)
        def _migration_preview_finished(self, preview: Any, error: Any):
            self.busy = False
            self.migration_preview_button.setEnabled(True)
            self.migration_source_button.setEnabled(True)
            self.migration_destination_button.setEnabled(True)
            if error is not None:
                self.migration_preview = None
                self.migration_status.setText(
                    "Migration preview failed (" + type(error).__name__ + "). No files were changed."
                )
                self.migration_preview_view.clear()
                if isinstance(error, PermissionError):
                    self._return_to_login()
                return
            self.migration_preview = preview
            lines = [
                "READ-ONLY MIGRATION PREVIEW",
                "",
                f"Source: {preview.source}",
                f"Destination: {preview.destination}",
                f"Files: {len(preview.files)}",
                f"Total source size: {preview.total_bytes:,} bytes",
                "",
                "Files to be copied:",
            ]
            lines.extend(f"  • {name}" for name in preview.files[:200])
            if len(preview.files) > 200:
                lines.append(
                    f"  … {len(preview.files) - 200} additional files are not listed in this preview"
                )
            lines.extend([
                "",
                "SQLite databases are snapshotted and integrity-checked.",
                "Other files are SHA-256 verified.",
                "The original source is retained.",
                "The destination must remain empty until migration starts.",
                "Initial safety limits: 20,000 files and 2 GiB total.",
                "For more than 200 files, only the first 200 paths are displayed.",
            ])
            self.migration_preview_view.setPlainText("\
".join(lines))
            self.migration_status.setText(
                "Preview complete. Review the source, destination, size, file count and displayed paths."
            )
            self.migration_execute_button.setEnabled(True)

        def _execute_migration(self):
            if self.controller is None or self.busy or self.migration_preview is None:
                return
            preview = self.migration_preview
            if (
                preview.source != Path(self.migration_source.text()).expanduser().resolve()
                or preview.destination != Path(self.migration_destination.text()).expanduser().resolve()
            ):
                self._clear_migration_preview()
                self.migration_status.setText("Paths changed. Generate a new preview.")
                return
            answer = QMessageBox.warning(
                self, "Confirm Yuki data migration",
                f"Copy {len(preview.files)} files ({preview.total_bytes:,} bytes) "
                f"from the selected source into a separate destination?\
\
"
                "The original data will be retained. Existing destination data will not be overwritten. "
                "This will not replace the currently running Yuki profile.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            source = str(preview.source)
            destination = str(preview.destination)
            controller = self.controller
            self.busy = True
            self.migration_preview_button.setEnabled(False)
            self.migration_execute_button.setEnabled(False)
            self.migration_source_button.setEnabled(False)
            self.migration_destination_button.setEnabled(False)
            self.migration_status.setText("Copying, verifying and publishing migrated data…")
            worker = BackupWorker(
                lambda: controller.migrate_data(source, destination)
            )
            worker.signals.finished.connect(self._migration_finished)
            self.pool.start(worker)

        @Slot(object, object)
        def _migration_finished(self, result: Any, error: Any):
            self.busy = False
            self.migration_preview_button.setEnabled(True)
            self.migration_source_button.setEnabled(True)
            self.migration_destination_button.setEnabled(True)
            self.migration_execute_button.setEnabled(False)
            if error is not None:
                self.migration_preview = None
                self.migration_status.setText(
                    "Migration failed (" + type(error).__name__ + "). "
                    "The original source was retained; review the destination before retrying."
                )
                if isinstance(error, PermissionError):
                    self._return_to_login()
                return
            self.migration_preview = None
            self.migration_status.setText(
                f"Migration verified: {len(result['files'])} files. "
                f"Destination: {self.migration_destination.text()}. "
                "Original source retained."
            )
            self.migration_preview_view.appendPlainText(
                "\
\
MIGRATION COMPLETE — verified manifest written to destination."
            )

        def _choose_backup_path(self):
            path, _ = QFileDialog.getSaveFileName(
                self, "Create Yuki backup", "Yuki-backup.yukibak",
                "Yuki encrypted backup (*.yukibak);;All files (*)",
            )
            if path:
                self.backup_path.setText(path)

        def _choose_restore_path(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Yuki backup", "",
                "Yuki encrypted backup (*.yukibak);;All files (*)",
            )
            if path:
                self.restore_path.setText(path)

        def _choose_restore_destination(self):
            path = QFileDialog.getExistingDirectory(self, "Choose an empty restore folder")
            if path:
                self.restore_destination.setText(path)

        def _run_backup_operation(self, operation, label):
            if self.controller is None or self.busy:
                return
            self.busy = True
            self.backup_create_button.setEnabled(False)
            self.backup_restore_button.setEnabled(False)
            self.backup_status.setText(label)
            worker = BackupWorker(operation)
            worker.signals.finished.connect(self._backup_finished)
            self.pool.start(worker)

        def _create_backup(self):
            if self.controller is None:
                return
            destination = self.backup_path.text().strip()
            passphrase = self.backup_passphrase.text()
            confirmation = self.backup_confirmation.text()
            self.backup_passphrase.clear()
            self.backup_confirmation.clear()
            if not destination:
                self.backup_status.setText("Choose a new backup file first.")
                return
            if len(passphrase) < 12 or len(passphrase) > 1024:
                self.backup_status.setText("Passphrase must contain 12-1024 characters.")
                return
            if passphrase != confirmation:
                self.backup_status.setText("Passphrases do not match. Nothing was written.")
                return
            answer = QMessageBox.question(
                self, "Create encrypted backup",
                "Yuki will create an encrypted copy of its local profile. "
                + ("The portable memory key will be included inside the encrypted archive if saved encrypted memory exists. " if self.include_memory_key.isChecked() else "Memory will remain tied to the source OS keyring. ")
                + "Keep the passphrase safe; it cannot be recovered. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            controller = self.controller
            include_memory_key = self.include_memory_key.isChecked()
            self._run_backup_operation(
                lambda: controller.create_backup(
                    destination, passphrase,
                    include_memory_key=include_memory_key,
                ),
                "Creating and verifying encrypted backup…",
            )

        def _restore_backup(self):
            if self.controller is None:
                return
            backup = self.restore_path.text().strip()
            destination = self.restore_destination.text().strip()
            passphrase = self.restore_passphrase.text()
            self.restore_passphrase.clear()
            if not backup or not destination or not passphrase:
                self.backup_status.setText("Choose a backup, an empty destination, and its passphrase.")
                return
            answer = QMessageBox.warning(
                self, "Restore backup",
                "Restore will create a separate YUKI_HOME package with .yuki/ profile data and workspace/ files. "
                "To use it later, configure YUKI_HOME to the selected folder and point the workspace at its workspace/ child. "
                + ("If this backup contains a portable memory key, Yuki will import it into this computer's OS keyring. " if self.import_memory_key.isChecked() else "Portable memory key import is disabled. ")
                + "The running profile is unchanged. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            controller = self.controller
            import_memory_key = self.import_memory_key.isChecked()
            self._run_backup_operation(
                lambda: controller.restore_backup(
                    backup, destination, passphrase,
                    import_memory_key=import_memory_key,
                ),
                "Authenticating and restoring backup into the selected folder…",
            )

        @Slot(object, object)
        def _backup_finished(self, result: Any, error: Any):
            self.busy = False
            self.backup_create_button.setEnabled(True)
            self.backup_restore_button.setEnabled(True)
            if error is not None:
                self.backup_status.setText(
                    "Backup operation failed (" + type(error).__name__ + "). "
                    "No existing profile data was overwritten."
                )
                if isinstance(error, PermissionError):
                    self._return_to_login()
                return
            if "path" in result:
                self.backup_status.setText(
                    f"Encrypted backup verified: {result['file_count']} files, "
                    f"{result['size_bytes']} bytes. Output: {result['path']}"
                    + (" Portable memory key included." if result.get("memory_key_included") else " Memory remains tied to the source OS keyring.")
                )
            else:
                self.backup_status.setText(
                    f"Restore verified: {result['file_count']} files, "
                    f"{result['size_bytes']} bytes. Destination: {result['destination']}"
                    + (" Portable memory key imported." if result.get("memory_key_imported") else "")
                )

        def _initialize(self):
            try:
                (
                    self.auth, self.sessions, self.provider,
                    self.coordinator, self.runtime, self.lifecycle,
                ) = _build_services()
                identity = self.sessions.current_identity()
                if identity is not None:
                    self._open_session(identity)
            except Exception as exc:
                self.login_button.setEnabled(False)
                from core.sqlite_recovery import (
                    SQLiteRecoveryRequired, format_sqlite_recovery_report,
                )
                if isinstance(exc, SQLiteRecoveryRequired):
                    self.login_status.setText(format_sqlite_recovery_report(exc.report))
                else:
                    self.login_status.setText(
                        "Desktop setup is unavailable (" + type(exc).__name__ + "). "
                        "Check the OS credential store and local configuration."
                    )

        def _login(self):
            if self.sessions is None:
                return
            self.login_button.setEnabled(False)
            username = self.username.text().strip()
            password = self.password.text()
            self.password.clear()
            identity = None
            try:
                identity = self.sessions.login(username, password)
                self._open_session(identity)
            except Exception as exc:
                if identity is not None:
                    try:
                        self.sessions.logout()
                    except Exception:
                        pass
                self.login_status.setText(
                    "Sign-in failed (" + type(exc).__name__ + "). Check your credentials "
                    "and the operating-system credential store."
                )
            finally:
                self.login_button.setEnabled(True)

        def _open_session(self, identity):
            # A fresh authenticated session restores the chat controls and tabs
            # after any prior fail-closed cleanup-retry state.
            for index in range(self.authenticated_tabs.count()):
                self.authenticated_tabs.setTabEnabled(index, True)
            for widget in (
                self.memory_toggle, self.transcript, self.message, self.send_button,
                self.voice_checkbox, self.voice_status,
                self.spoken_replies_checkbox, self.speech_stop_button,
            ):
                widget.setEnabled(True)
            if self.voice_session is not None:
                self.voice_session.close()
            self.voice_session = None
            self.voice_checkbox.blockSignals(True)
            self.voice_checkbox.setChecked(False)
            self.voice_checkbox.blockSignals(False)
            self.voice_button.setEnabled(False)
            self.voice_stop_button.setEnabled(False)
            if self.speech_session is not None:
                try:
                    self.speech_session.close()
                except Exception as exc:
                    # A stale audio backend must not block a new authenticated
                    # session from being initialized.
                    self.chat_status.setText(
                        "Warning: previous speech session cleanup failed ("
                        + type(exc).__name__ + ")."
                    )
            self.speech_session = None
            self.spoken_replies_checkbox.blockSignals(True)
            self.spoken_replies_checkbox.setChecked(False)
            self.spoken_replies_checkbox.blockSignals(False)
            self.spoken_replies_checkbox.setEnabled(True)
            self.speech_stop_button.setEnabled(False)
            self.voice_status.setText("Microphone off. Spoken replies off. No background listening.")
            self.logout_button.setText("Sign out")
            config = load_config()
            memory = create_desktop_memory(self.sessions.keyring)
            context = build_desktop_context(
                authenticate=self.sessions.current_identity,
                provider=self.provider,
                memory=memory,
                session=ConversationSession(self.provider),
                agent_coordinator=self.coordinator,
                task_runtime=self.runtime,
            )
            if context.identity != identity:
                raise DesktopStartupError("The authenticated desktop session changed.")
            specialist_controller = DesktopCompanionController(
                context.application, context.identity, self.sessions,
                workspace_path=Path(config["workspace"]),
            )
            # Durable chat uses the canonical owner-scoped conversation runtime.
            # The existing specialist controller remains the authority for task
            # approvals, backups, restore and migration during this cutover.
            from ui.desktop.shared_desktop_composition import build_shared_desktop_controller
            self.controller = build_shared_desktop_controller(
                config,
                provider=self.provider,
                identity=context.identity,
                sessions=self.sessions,
                specialist_controller=specialist_controller,
                memory=context.application.memory,
            )
            self.identity_label.setText("Signed in · " + identity.actor_id[:12])
            self.memory_toggle.setChecked(False)
            self.transcript.clear()
            self.chat_status.setText("Connected to your configured provider.")
            self.pages.setCurrentIndex(1)
            self._refresh_specialists()
            self._refresh_tasks()
            self.message.setFocus()

        def _set_memory_context(self, enabled: bool):
            if self.controller is None:
                return
            try:
                self.controller.set_memory_context(enabled)
                self.chat_status.setText(
                    "Saved-memory context enabled for this session."
                    if enabled else "Saved-memory context disabled."
                )
            except Exception as exc:
                self.memory_toggle.blockSignals(True)
                self.memory_toggle.setChecked(False)
                self.memory_toggle.blockSignals(False)
                self.chat_status.setText("Session unavailable (" + type(exc).__name__ + ").")
                self._return_to_login()

        def _refresh_specialists(self):
            if self.controller is None:
                return
            try:
                self.specialist_list.clear()
                for specialist in self.controller.list_specialists():
                    capabilities = ", ".join(specialist.get("capabilities", ()))
                    status = "enabled" if specialist.get("enabled") else "disabled"
                    item = QListWidgetItem(
                        f'{specialist.get("name", "Specialist")} '
                        f'[{specialist.get("agent_id", "")}] — {status}\
'
                        f'Capabilities: {capabilities}'
                    )
                    self.specialist_list.addItem(item)
            except Exception as exc:
                self.chat_status.setText("Specialist catalog unavailable (" + type(exc).__name__ + ").")
                if isinstance(exc, PermissionError):
                    self._return_to_login()

        def _propose_plan(self):
            if self.controller is None or self.busy:
                return
            goal = self.plan_goal.text().strip()
            if not goal:
                self.plan_details.setPlainText("Enter a goal before requesting a plan.")
                return
            self.busy = True
            self.plan_propose_button.setEnabled(False)
            self.plan_dispatch_button.setEnabled(False)
            self.plan_details.setPlainText("Yuki is preparing a proposal…")
            worker = PlanWorker(self.controller, goal)
            worker.signals.finished.connect(self._plan_finished)
            self.pool.start(worker)

        @Slot(object, object)
        def _plan_finished(self, plan: Any, error: Any):
            self.busy = False
            self.plan_propose_button.setEnabled(True)
            if error is not None:
                self.plan_details.setPlainText(
                    "Plan proposal failed (" + type(error).__name__ + ")."
                )
                if isinstance(error, PermissionError):
                    self._return_to_login()
                return
            self.current_plan = plan
            lines = [f"Plan ID: {plan.plan_id}", "", "Review each proposed subtask:"]
            for task in plan.subtasks:
                lines.extend([
                    f"• {task.subtask_id} → {task.agent_id}",
                    f"  {task.description}",
                    f"  Capabilities: {', '.join(task.required_capabilities)}",
                    f"  Permissions: {', '.join(task.required_permissions) or 'none declared'}",
                    f"  Dependencies: {', '.join(task.depends_on) or 'none'}",
                    f"  Approval required: {'yes' if task.requires_approval else 'no'}",
                    f"  Status: {task.status}",
                    "",
                ])
            self.plan_details.setPlainText("\
".join(lines))
            self.plan_dispatch_button.setEnabled(
                bool(plan.subtasks) and all(task.status == "pending" for task in plan.subtasks)
            )
            self.plan_cancel_button.setEnabled(True)

        def _dispatch_plan(self):
            if self.controller is None or self.current_plan is None or self.busy:
                return
            answer = QMessageBox.question(
                self, "Dispatch plan",
                "Dispatch this reviewed plan? This queues eligible tasks but does not start workers.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                plan = self.controller.dispatch_agent_plan(self.current_plan.plan_id)
                self.current_plan = plan
                self._plan_finished(plan, None)
                self.plan_dispatch_button.setEnabled(False)
                self._refresh_tasks()
            except Exception as exc:
                QMessageBox.warning(self, "Dispatch failed", "The plan was not dispatched (" + type(exc).__name__ + ").")

        def _cancel_plan(self):
            if self.controller is None or self.current_plan is None:
                return
            answer = QMessageBox.question(
                self, "Cancel plan",
                "Cancel this plan and its pending/dispatched subtasks?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                self.current_plan = self.controller.cancel_agent_plan(self.current_plan.plan_id)
                self._plan_finished(self.current_plan, None)
                self.plan_dispatch_button.setEnabled(False)
                self.plan_cancel_button.setEnabled(False)
                self._refresh_tasks()
            except Exception as exc:
                QMessageBox.warning(self, "Cancellation failed", "The plan could not be cancelled (" + type(exc).__name__ + ").")

        def _refresh_tasks(self):
            if self.controller is None:
                return
            try:
                self.task_list.clear()
                for task in self.controller.list_tasks(limit=50):
                    item = QListWidgetItem(
                        f"{task.name} — {task.state} — v{task.version} — "
                        f"{task.task_id}\
Updated: {task.updated_at}"
                    )
                    item.setData(256, (task.task_id, task.name, task.state, task.version))
                    self.task_list.addItem(item)
                self._update_task_actions()
            except Exception as exc:
                self.chat_status.setText("Task list unavailable (" + type(exc).__name__ + ").")
                if isinstance(exc, PermissionError):
                    self._return_to_login()

        def _update_task_actions(self, *_args):
            item = self.task_list.currentItem()
            if item is None:
                self.task_approve_button.setEnabled(False)
                self.task_cancel_button.setEnabled(False)
                return
            _task_id, _name, state, _version = item.data(256)
            self.task_approve_button.setEnabled(state == "waiting_approval")
            self.task_cancel_button.setEnabled(state in {"queued", "waiting_approval", "running"})

        def _show_selected_progress(self):
            if self.controller is None:
                return
            item = self.task_list.currentItem()
            if item is None:
                return
            task_id, name, state, version = item.data(256)
            try:
                updates = self.controller.task_progress(task_id, after_sequence=0, limit=50)
                if not updates:
                    self.task_progress_view.setPlainText("No progress events recorded yet.")
                    return
                lines = [
                    f"{update.sequence}. {update.kind}: {update.message}"
                    + (f" ({update.percent}%)" if update.percent is not None else "")
                    for update in updates
                ]
                self.task_progress_view.setPlainText("\
".join(lines))
            except Exception as exc:
                self.task_progress_view.setPlainText(
                    "Task progress unavailable (" + type(exc).__name__ + ")."
                )
                if isinstance(exc, PermissionError):
                    self._return_to_login()

        def _review_and_approve_task(self):
            if self.controller is None:
                return
            item = self.task_list.currentItem()
            if item is None:
                return
            task_id, name, state, version = item.data(256)
            if state != "waiting_approval":
                QMessageBox.information(
                    self, "Approval unavailable",
                    "Only tasks explicitly waiting for approval can be approved."
                )
                return
            try:
                task = self.controller.get_task(task_id)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Task review unavailable",
                    "The task definition could not be loaded (" + type(exc).__name__ + ")."
                )
                if isinstance(exc, PermissionError):
                    self._return_to_login()
                return
            if task.state != "waiting_approval" or task.version != version:
                QMessageBox.warning(
                    self, "Task changed",
                    "This task changed since the list was loaded. Refresh and review the latest version."
                )
                self._refresh_tasks()
                return

            lines = [
                "Review the exact stored workflow definition before approving.",
                "",
                f"Task: {task.name}",
                f"Task ID: {task.task_id}",
                f"State: {task.state}",
                f"Version: {task.version}",
                "",
            ]
            for index, step in enumerate(task.steps, start=1):
                lines.extend([
                    f"STEP {index}: {step.get('name', 'unnamed')}",
                    f"Action: {step.get('action', 'unspecified')}",
                    f"Argument: {step.get('argument', '')!r}",
                    f"Requires approval: {step.get('requires_approval', True)}",
                    "Required permissions: "
                    + (", ".join(step.get("required_permissions", ())) or "none declared"),
                    "",
                ])
            lines.extend([
                "Approval authorizes this exact workflow definition for the current session.",
                "It does not itself start a worker. A separate authorized worker is required.",
                "",
                "Do not approve if an action, argument, target, or permission is unexpected.",
            ])
            dialog = QDialog(self)
            dialog.setWindowTitle("Review task approval")
            dialog.setMinimumSize(640, 480)
            layout = QVBoxLayout(dialog)
            review = QPlainTextEdit()
            review.setReadOnly(True)
            review.setPlainText("\
".join(lines))
            layout.addWidget(review, 1)
            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            )
            buttons.button(QDialogButtonBox.Ok).setText("Approve exact task")
            buttons.button(QDialogButtonBox.Ok).setEnabled(True)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.Accepted:
                return
            try:
                self.controller.approve_task(task_id, expected_version=version)
                QMessageBox.information(
                    self, "Task approved",
                    "Approval was recorded for this task version and current session. "
                    "No worker was started."
                )
                self._refresh_tasks()
            except Exception as exc:
                QMessageBox.warning(
                    self, "Approval failed",
                    "The task was not approved (" + type(exc).__name__ + "). "
                    "It may have changed; refresh and review again."
                )
                self._refresh_tasks()

        def _cancel_selected_task(self):
            if self.controller is None:
                return
            item = self.task_list.currentItem()
            if item is None:
                return
            task_id, name, state, version = item.data(256)
            answer = QMessageBox.question(
                self, "Cancel task",
                f"Cancel task '{name}'?\
ID: {task_id}\
State: {state}\
Version: {version}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                self.controller.cancel_task(task_id, expected_version=version)
                self._refresh_tasks()
            except Exception as exc:
                QMessageBox.warning(
                    self, "Task not cancelled",
                    "The task changed or could not be cancelled (" + type(exc).__name__ + "). Refresh and review it again."
                )

        def _set_voice_enabled(self, enabled: bool):
            if not enabled:
                session = self.voice_session
                self.voice_session = None
                if session is not None:
                    session.disable()
                self.voice_button.setEnabled(False)
                self.voice_stop_button.setEnabled(False)
                self.voice_status.setText("Microphone off. No background listening.")
                return
            answer = QMessageBox.question(
                self, "Enable microphone for this session",
                "Yuki will access the microphone only after you press Speak. "
                "Each capture is limited to five seconds and can be stopped. "
                "Audio is held temporarily for local transcription and then removed. "
                "The first use may download the local speech-recognition model. "
                "This does not enable wake-word or background listening. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.voice_checkbox.blockSignals(True)
                self.voice_checkbox.setChecked(False)
                self.voice_checkbox.blockSignals(False)
                return
            try:
                from voice.session import VoiceSession
                self.voice_session = VoiceSession(enabled=True)
                self.voice_button.setEnabled(self.controller is not None and not self.busy)
                self.voice_status.setText(
                    "Microphone permission enabled for this session. Press Speak to start a five-second capture."
                )
            except Exception as exc:
                self.voice_session = None
                self.voice_checkbox.blockSignals(True)
                self.voice_checkbox.setChecked(False)
                self.voice_checkbox.blockSignals(False)
                self.voice_status.setText("Voice setup unavailable (" + type(exc).__name__ + ").")

        def _set_spoken_replies_enabled(self, enabled: bool):
            if not enabled:
                session = self.speech_session
                self.speech_session = None
                if session is not None:
                    try:
                        session.close()
                    except Exception as exc:
                        self.voice_status.setText(
                            "Spoken-reply shutdown failed (" + type(exc).__name__ + ")."
                        )
                self.speech_stop_button.setEnabled(False)
                if self.speech_worker is None and self.controller is not None:
                    self.send_button.setEnabled(not self.busy)
                    self.message.setEnabled(not self.busy)
                if self.voice_status.text().startswith("Spoken-reply shutdown failed"):
                    return
                self.voice_status.setText("Spoken replies off. Microphone permission is independent.")
                return
            if self.controller is None:
                self.spoken_replies_checkbox.blockSignals(True)
                self.spoken_replies_checkbox.setChecked(False)
                self.spoken_replies_checkbox.blockSignals(False)
                return
            answer = QMessageBox.question(
                self, "Enable spoken replies",
                "Yuki will speak its text replies using the configured local TTS backend. "
                "Speech is generated on this device and runs in a background worker. "
                "No microphone access or background listening is enabled by this setting. "
                "You can stop playback or disable spoken replies at any time. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.spoken_replies_checkbox.blockSignals(True)
                self.spoken_replies_checkbox.setChecked(False)
                self.spoken_replies_checkbox.blockSignals(False)
                return
            try:
                from voice.desktop_output import create_desktop_voice_output
                self.speech_session = create_desktop_voice_output(load_config())
                self.speech_session.enable()
                self.voice_status.setText(
                    "Spoken replies enabled for this sign-in session. "
                    "Microphone permission remains separate."
                )
            except Exception as exc:
                self.speech_session = None
                self.spoken_replies_checkbox.blockSignals(True)
                self.spoken_replies_checkbox.setChecked(False)
                self.spoken_replies_checkbox.blockSignals(False)
                self.voice_status.setText(
                    "Spoken replies unavailable (" + type(exc).__name__ + ")."
                )

        def _stop_spoken_reply(self):
            session = self.speech_session
            if session is None:
                return
            try:
                session.stop()
                self.speech_stop_button.setEnabled(False)
                self.voice_status.setText("Stopping spoken reply…")
            except Exception as exc:
                self.voice_status.setText(
                    "Speech stop failed (" + type(exc).__name__ + ")."
                )

        def _start_spoken_reply(self, reply: str):
            session = self.speech_session
            if session is None or not session.enabled or not reply.strip():
                return
            if self.speech_worker is not None:
                return
            worker = SpeechWorker(session, reply)
            worker.signals.finished.connect(self._spoken_reply_finished)
            self.speech_worker = worker
            self.speech_stop_button.setEnabled(True)
            self.send_button.setEnabled(False)
            self.message.setEnabled(False)
            self.voice_status.setText("Yuki is speaking. Press Stop speaking to interrupt.")
            self.pool.start(worker)

        @Slot(object, object, object, object)
        def _spoken_reply_finished(
            self, result: Any, error: Any, captured_session: Any, completed_worker: Any,
        ):
            if self.speech_worker is completed_worker:
                self.speech_worker = None
            if captured_session is not self.speech_session:
                # A revoked worker may finish after a later login/session has
                # started. Never let its callback overwrite a newer worker/UI.
                if (
                    self.speech_worker is None
                    and self.controller is not None
                    and not self._logout_started
                    and not self._shutdown_started
                ):
                    self.speech_stop_button.setEnabled(False)
                    self.send_button.setEnabled(not self.busy)
                    self.message.setEnabled(not self.busy)
                return
            self.speech_stop_button.setEnabled(False)
            if self.controller is not None and not self._logout_started and not self._shutdown_started:
                self.send_button.setEnabled(not self.busy)
                self.message.setEnabled(not self.busy)
            if error is not None:
                self.voice_status.setText(
                    "Spoken reply stopped or unavailable (" + type(error).__name__ + ")."
                )
            elif result is not None and result.get("interrupted"):
                self.voice_status.setText("Spoken reply interrupted.")
            elif self.speech_session is not None and self.speech_session.enabled:
                self.voice_status.setText("Spoken reply finished.")

        def _start_voice_capture(self):
            if self.controller is None or self.busy or self.voice_session is None:
                return
            if not self.voice_session.enabled:
                self.voice_status.setText("Enable microphone permission before starting a capture.")
                return
            self.busy = True
            self.send_button.setEnabled(False)
            self.message.setEnabled(False)
            self.voice_button.setEnabled(False)
            self.voice_stop_button.setEnabled(True)
            self.voice_status.setText(
                "Microphone active — capture ends within five seconds, then local transcription begins."
            )
            self.chat_status.setText("Voice capture started.")
            worker = VoiceWorker(self.voice_session)
            worker.signals.phase.connect(self._voice_phase_changed)
            worker.signals.finished.connect(self._voice_finished)
            self.pool.start(worker)

        @Slot(str)
        def _voice_phase_changed(self, phase: str):
            if phase == "transcribing":
                self.voice_stop_button.setEnabled(False)
                self.voice_status.setText(
                    "Microphone is off. Transcribing locally; you can still disable voice to discard this result."
                )
                self.chat_status.setText("Transcribing locally. Review the transcript before sending it.")

        def _stop_voice_capture(self):
            if self.voice_session is None or not self.busy:
                return
            self.voice_session.cancel()
            self.voice_stop_button.setEnabled(False)
            self.voice_status.setText("Stopping microphone capture…")

        @Slot(str, object, object)
        def _voice_finished(self, transcript: str, error: Any, captured_session: Any):
            self.busy = False
            self.send_button.setEnabled(True)
            self.message.setEnabled(True)
            self.voice_stop_button.setEnabled(False)
            self.voice_button.setEnabled(
                self.voice_session is not None and self.voice_session.enabled
            )
            # Disabling/replacing a voice session revokes permission and also
            # invalidates any result whose worker completion was already queued.
            if captured_session is not self.voice_session or not captured_session.enabled:
                self.voice_status.setText("Microphone off. The revoked session's transcript was discarded.")
                self.chat_status.setText("Ready")
                return
            if error is not None:
                detail = str(error).strip()[:180] or type(error).__name__
                self.voice_status.setText("Voice capture stopped or failed: " + detail)
                self.chat_status.setText("Ready")
                return
            if not transcript.strip():
                self.voice_status.setText("No speech was recognized. Try again or type a message.")
                self.chat_status.setText("Ready")
                return
            self.message.setText(transcript)
            self.message.setFocus()
            self.voice_status.setText("Transcript ready. Review it, then press Send to share it with Yuki.")
            self.chat_status.setText("Transcript was produced locally; nothing was sent yet.")

        def _send(self):
            if self.controller is None or self.busy:
                return
            prompt = self.message.text().strip()
            if not prompt:
                return
            self.busy = True
            self.message.clear()
            self.send_button.setEnabled(False)
            self.message.setEnabled(False)
            self.voice_button.setEnabled(False)
            self.memory_toggle.setEnabled(False)
            self.plan_propose_button.setEnabled(False)
            self.transcript.appendPlainText("You: " + prompt)
            self.chat_status.setText("Yuki is thinking…")
            worker = ChatWorker(self.controller, prompt)
            worker.signals.finished.connect(self._chat_finished)
            self.pool.start(worker)

        @Slot(str, object)
        def _chat_finished(self, reply: str, error: Any):
            self.busy = False
            self.send_button.setEnabled(True)
            self.message.setEnabled(True)
            self.voice_button.setEnabled(
                self.voice_session is not None and self.voice_session.enabled
            )
            self.memory_toggle.setEnabled(True)
            self.plan_propose_button.setEnabled(True)
            if error is not None:
                self.chat_status.setText("Request failed (" + type(error).__name__ + ").")
                if isinstance(error, PermissionError):
                    self._return_to_login()
            else:
                self.transcript.appendPlainText("Yuki: " + reply)
                self.chat_status.setText("Ready")
                self._start_spoken_reply(reply)

        def _clear_authenticated_session_ui(self):
            if self.voice_session is not None:
                self.voice_session.close()
            self.voice_session = None
            if self.speech_session is not None:
                try:
                    self.speech_session.close()
                except Exception as exc:
                    # Continue clearing authenticated UI even if the audio
                    # backend reports an interruption failure.
                    self.chat_status.setText(
                        "Warning: speech backend cleanup failed (" + type(exc).__name__ + ")."
                    )
            self.speech_session = None
            self.speech_worker = None
            self.spoken_replies_checkbox.blockSignals(True)
            self.spoken_replies_checkbox.setChecked(False)
            self.spoken_replies_checkbox.blockSignals(False)
            self.spoken_replies_checkbox.setEnabled(False)
            self.speech_stop_button.setEnabled(False)
            self.voice_checkbox.blockSignals(True)
            self.voice_checkbox.setChecked(False)
            self.voice_checkbox.blockSignals(False)
            self.voice_button.setEnabled(False)
            self.voice_stop_button.setEnabled(False)
            self.voice_status.setText("Microphone off. No background listening.")
            self.memory_toggle.blockSignals(True)
            self.memory_toggle.setChecked(False)
            self.memory_toggle.blockSignals(False)
            self.transcript.clear()
            self.specialist_list.clear()
            self.task_list.clear()
            self.task_progress_view.clear()
            self.backup_path.clear()
            self.backup_passphrase.clear()
            self.backup_confirmation.clear()
            self.restore_path.clear()
            self.restore_destination.clear()
            self.restore_passphrase.clear()
            self.migration_source.clear()
            self.migration_destination.clear()
            self._clear_migration_preview()
            self.migration_source_button.setEnabled(True)
            self.migration_destination_button.setEnabled(True)
            self.migration_status.setText("Sign in to use migration.")
            self.backup_status.setText("Sign in to use backup and restore.")
            self.task_approve_button.setEnabled(False)
            self.task_cancel_button.setEnabled(False)
            self.current_plan = None
            self.plan_goal.clear()
            self.plan_details.clear()
            self.plan_dispatch_button.setEnabled(False)
            self.plan_cancel_button.setEnabled(False)


        def _return_to_login(self):
            controller = self.controller
            cleanup_error = _close_expired_controller(controller)
            self._clear_authenticated_session_ui()
            if cleanup_error is not None:
                # Keep the closed controller reachable so cleanup can be retried.
                # Only the sign-out action remains enabled; the bridge rejects all
                # chat and specialist operations while its authorization gate is shut.
                self.pages.setCurrentIndex(1)
                self._restrict_to_logout_retry()
                self.chat_status.setText(
                    "Session expired; cleanup is incomplete. Retry sign out before signing in again."
                )
                self.login_status.setText(
                    "Local session cleanup is incomplete ("
                    + type(cleanup_error).__name__ + "). Retry sign out."
                )
                return
            self.controller = None
            self.pages.setCurrentIndex(0)

        def _restrict_to_logout_retry(self):
            self.setEnabled(True)
            _restrict_session_to_logout_retry(
                self.authenticated_tabs,
                (
                    self.memory_toggle, self.transcript, self.message, self.send_button,
                    self.voice_checkbox, self.voice_button, self.voice_stop_button,
                    self.voice_status,
                ),
                self.logout_button,
                self.chat_status,
            )

        @Slot(object)
        def _logout_finished(self, error: Any):
            self._logout_started = False
            self._logout_worker = None
            if error is not None:
                # Keep the closed bridge attached so a second sign-out attempt
                # can retry any cleanup step that failed. Returning to login and
                # discarding it here would make the retry path unreachable.
                self._clear_authenticated_session_ui()
                self.logout_button.setText("Retry sign out")
                self.chat_status.setText(
                    "Sign-out cleanup incomplete. Other session actions are blocked; retry sign out."
                )
                if not self._shutdown_started:
                    self._restrict_to_logout_retry()
                QMessageBox.warning(
                    self, "Sign out",
                    "Session cleanup did not fully complete (" + type(error).__name__ + "). "
                    "Yuki has closed the session action gate. Retry sign out to finish cleanup."
                )
                return
            self.controller = None
            self._return_to_login()
            if not self._shutdown_started:
                self.setEnabled(True)
            self.logout_button.setText("Sign out")
            self.login_status.setText("You are signed out.")

        def _logout(self):
            if self._logout_started or self._shutdown_started:
                return
            self._logout_started = True
            if self.voice_session is not None:
                self.voice_session.close()
            if self.speech_session is not None:
                try:
                    self.speech_session.close()
                except Exception as exc:
                    # Do not strand the application in a half-started shutdown
                    # because an audio driver refused to stop.
                    self.chat_status.setText(
                        "Warning: speech backend cleanup failed (" + type(exc).__name__ + ")."
                    )
            self.speech_session = None
            self.setEnabled(False)
            self.login_status.setText(
                "Yuki is finishing active work before signing out…"
            )
            worker = LogoutWorker(self.controller, self.sessions)
            worker.signals.finished.connect(self._logout_finished)
            self._logout_worker = worker
            # The single-thread pool guarantees this runs after the active
            # request, so logout cannot close its runtime underneath inference.
            self.pool.start(worker)

        @Slot(object)
        def _shutdown_finished(self, error: Any):
            if error is not None:
                # Keep the controller and lifecycle attached. Their failed
                # cleanup hooks are retryable; do not exit with revocation
                # incomplete or silently discard the only retry handle.
                self._shutdown_started = False
                self.setEnabled(True)
                self.login_status.setText(
                    "Shutdown cleanup incomplete. Session actions remain blocked. "
                    "Close Yuki again to retry cleanup."
                )
                QMessageBox.warning(
                    self, "Yuki shutdown",
                    "Yuki could not complete every cleanup operation ("
                    + type(error).__name__
                    + "). Successful steps were retained; close Yuki again to retry failed cleanup."
                )
                return
            self._shutdown_complete = True
            self.controller = None
            self.close()

        def closeEvent(self, event):
            if self._shutdown_complete:
                event.accept()
                return
            event.ignore()
            if self._shutdown_started:
                return
            self._shutdown_started = True
            if self.voice_session is not None:
                self.voice_session.close()
            if self.speech_session is not None:
                try:
                    self.speech_session.close()
                except Exception as exc:
                    # Audio-driver failures must not prevent the application
                    # from revoking the authenticated session and shutting down.
                    self.chat_status.setText(
                        "Warning: speech backend cleanup failed (" + type(exc).__name__ + ")."
                    )
            self.speech_session = None
            self.setEnabled(False)
            self.login_status.setText(
                "Yuki is safely signing out and shutting down…"
            )
            # The pool is single-threaded: this shutdown task runs only after
            # any active provider/backup/plan operation has returned. Resource
            # closure therefore cannot race an in-flight desktop operation.
            worker = ShutdownWorker(self.lifecycle, self.controller, self.sessions)
            worker.signals.finished.connect(self._shutdown_finished)
            self._shutdown_worker = worker
            self.pool.start(worker)

    app = QApplication.instance() or QApplication(sys.argv)
    window = YukiWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
