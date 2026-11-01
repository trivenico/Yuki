"""Native PySide6 desktop companion for Yuki.

Qt remains optional and is imported only inside run(). Provider work runs on a
single background worker; widgets never access provider clients or stores.
"""
from __future__ import annotations

import sys
from typing import Any


def _build_services():
    from core.auth import BuiltInAuthService
    from core.config import load_config
    from core.portable_paths import data_root
    from providers.factory import create_provider
    from ui.desktop.auth import DesktopSessionManager, load_os_keyring

    root = data_root() / ".yuki"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    auth = BuiltInAuthService(str(root / "auth.sqlite3"))
    sessions = DesktopSessionManager(auth, load_os_keyring())
    provider = create_provider(load_config())
    return auth, sessions, provider


def run() -> int:
    """Start the desktop login/chat UI, requiring the optional desktop extra."""
    try:
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
        from PySide6.QtWidgets import (
            QApplication, QCheckBox, QFormLayout, QHBoxLayout, QLabel,
            QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
            QStackedWidget, QVBoxLayout, QWidget,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Yuki Desktop requires the optional dependencies. "
            "Install them with: python -m pip install -r requirements-desktop.txt"
        ) from exc

    from ui.desktop.bootstrap import DesktopStartupError, build_desktop_context
    from ui.desktop.controller import DesktopCompanionController

    class WorkerSignals(QObject):
        finished = Signal(str, object)

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

    class YukiWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Yuki")
            self.setMinimumSize(820, 620)
            self.pool = QThreadPool.globalInstance()
            self.pool.setMaxThreadCount(1)
            self.auth = None
            self.sessions = None
            self.provider = None
            self.controller = None
            self.busy = False
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
            self.chat_status = QLabel("Ready")
            chat_layout.addWidget(self.chat_status)
            self.pages.addWidget(chat)

        def _initialize(self):
            try:
                self.auth, self.sessions, self.provider = _build_services()
                identity = self.sessions.current_identity()
                if identity is not None:
                    self._open_session(identity)
            except Exception as exc:
                self.login_button.setEnabled(False)
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
            context = build_desktop_context(
                authenticate=self.sessions.current_identity,
                provider=self.provider,
            )
            if context.identity != identity:
                raise DesktopStartupError("The authenticated desktop session changed.")
            self.controller = DesktopCompanionController(
                context.application, context.identity, self.sessions
            )
            self.identity_label.setText("Signed in · " + identity.actor_id[:12])
            self.memory_toggle.setChecked(False)
            self.transcript.clear()
            self.chat_status.setText("Connected to your configured provider.")
            self.pages.setCurrentIndex(1)
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
            if error is not None:
                self.chat_status.setText("Request failed (" + type(error).__name__ + ").")
                if isinstance(error, PermissionError):
                    self._return_to_login()
            else:
                self.transcript.appendPlainText("Yuki: " + reply)
                self.chat_status.setText("Ready")

        def _return_to_login(self):
            self.controller = None
            self.memory_toggle.blockSignals(True)
            self.memory_toggle.setChecked(False)
            self.memory_toggle.blockSignals(False)
            self.pages.setCurrentIndex(0)

        def _logout(self):
            if self.busy:
                QMessageBox.information(
                    self, "Request in progress",
                    "Wait for the current response before signing out."
                )
                return
            try:
                if self.controller is not None:
                    self.controller.logout()
                elif self.sessions is not None:
                    self.sessions.logout()
            except Exception as exc:
                QMessageBox.warning(
                    self, "Sign out",
                    "Session cleanup failed (" + type(exc).__name__ + ")."
                )
                return
            self.controller = None
            self._return_to_login()
            self.transcript.clear()
            self.login_status.setText("You are signed out.")

        def closeEvent(self, event):
            if self.busy:
                QMessageBox.information(
                    self, "Request in progress",
                    "Wait for the current response before closing Yuki."
                )
                event.ignore()
                return
            event.accept()

    app = QApplication.instance() or QApplication(sys.argv)
    window = YukiWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
