"""Native PySide6 desktop companion for Yuki.

Qt remains optional and is imported only inside run(). Provider work runs on a
single background worker; widgets never access provider clients or stores.
"""
from __future__ import annotations

import sys
from typing import Any


def _build_services():
    from core.auth import BuiltInAuthService, task_permission_authorizer
    from core.config import load_config
    from core.portable_paths import desktop_data_root
    from providers.factory import create_provider
    from companion.memory_consent import CompanionMemory
    from companion.conversation import ConversationSession
    from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
    from automation.task_runtime import TaskRuntime
    from automation.workflow_store import WorkflowRunStore
    from ui.desktop.auth import DesktopSessionManager, load_os_keyring

    root = desktop_data_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    auth = BuiltInAuthService(str(root / "auth.sqlite3"))
    sessions = DesktopSessionManager(auth, load_os_keyring())
    provider = create_provider(load_config())
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
    return auth, sessions, provider, coordinator, runtime


def run() -> int:
    """Start Yuki Desktop, or run a display-independent packaged-runtime smoke test."""
    if "--smoke-test" in sys.argv[1:]:
        try:
            from PySide6.QtWidgets import QApplication, QLabel
        except ImportError as exc:
            raise RuntimeError(
                "Yuki Desktop could not load PySide6 or its native Qt runtime libraries. "
                "Install requirements-desktop.txt and the platform's Qt system dependencies."
            ) from exc
        app = QApplication.instance() or QApplication([sys.argv[0]])
        probe = QLabel("Yuki Desktop runtime OK")
        probe.show()
        app.processEvents()
        if not probe.isVisible():
            raise RuntimeError("Qt could not initialize a visible desktop widget.")
        probe.close()
        app.processEvents()
        return 0

    try:
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
        from PySide6.QtWidgets import (
            QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
            QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
            QStackedWidget, QVBoxLayout, QWidget, QTabWidget,
            QListWidget, QListWidgetItem, QTextEdit,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Yuki Desktop requires the optional dependencies. "
            "Install them with: python -m pip install -r requirements-desktop.txt"
        ) from exc

    from companion.conversation import ConversationSession
    from companion.memory_consent import CompanionMemory
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
            self.controller = None
            self.busy = False
            self.current_plan = None
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

            self.pages.addWidget(tabs)

        def _initialize(self):
            try:
                (
                    self.auth, self.sessions, self.provider,
                    self.coordinator, self.runtime,
                ) = _build_services()
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
                memory=CompanionMemory(),
                session=ConversationSession(self.provider),
                agent_coordinator=self.coordinator,
                task_runtime=self.runtime,
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
                        f'[{specialist.get("agent_id", "")}] — {status}\\n'
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
            self.plan_details.setPlainText("\\n".join(lines))
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
                        f"{task.task_id}\\nUpdated: {task.updated_at}"
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
                self.task_progress_view.setPlainText("\\n".join(lines))
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
            review.setPlainText("\\n".join(lines))
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
                f"Cancel task '{name}'?\\nID: {task_id}\\nState: {state}\\nVersion: {version}",
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
            self.memory_toggle.setEnabled(True)
            self.plan_propose_button.setEnabled(True)
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
            self.transcript.clear()
            self.specialist_list.clear()
            self.task_list.clear()
            self.task_progress_view.clear()
            self.task_approve_button.setEnabled(False)
            self.task_cancel_button.setEnabled(False)
            self.current_plan = None
            self.plan_details.clear()
            self.plan_dispatch_button.setEnabled(False)
            self.plan_cancel_button.setEnabled(False)
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
            self.specialist_list.clear()
            self.task_list.clear()
            self.task_progress_view.clear()
            self.task_approve_button.setEnabled(False)
            self.current_plan = None
            self.plan_details.clear()
            self.plan_dispatch_button.setEnabled(False)
            self.plan_cancel_button.setEnabled(False)
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
