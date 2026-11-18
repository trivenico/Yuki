"""Explicit-consent Learning Center panel; storage and work remain owner-controlled."""
from __future__ import annotations

import re
from typing import Callable


_SAFE_ID = re.compile(r"[a-f0-9]{24}")
_SAFE_TASK = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _plain(value: object, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub("", value[:limit])


class LearningCenterPanel:
    """Factory for a passive Qt panel with explicit consent and review actions.

    No store, model, or audit log is created here. The panel runs supplied
    storage/audit callbacks on one bounded Qt worker at a time and marshals
    results back to the GUI thread. Approval requires both consent scopes.
    """

    @staticmethod
    def create(
        parent=None,
        *,
        on_consent: Callable[[str, bool], None],
        on_refresh: Callable[[], dict],
        on_review: Callable[[str], dict | None],
        on_approve: Callable[[str], None],
        on_reject: Callable[[str], None],
        on_delete_all: Callable[[], dict[str, int]],
        on_submit: Callable[[list[dict[str, str]], str, str], str] | None = None,
    ):
        from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
        from PySide6.QtWidgets import (
            QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
            QLabel, QListWidget, QListWidgetItem, QLineEdit, QMessageBox,
            QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
        )

        callbacks = (on_consent, on_refresh, on_review, on_approve, on_reject, on_delete_all)
        if on_submit is not None and not callable(on_submit):
            raise TypeError("on_submit must be callable when provided.")
        if not all(callable(callback) for callback in callbacks):
            raise TypeError("Learning Center callbacks must be callable.")

        class _LearningSignals(QObject):
            finished = Signal(object, object)

        class _LearningReceiver(QObject):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.completion = None

            @Slot(object, object)
            def dispatch(self, result, error):
                completion = self.completion
                self.completion = None
                if callable(completion):
                    completion(result, error)

        class _LearningWorker(QRunnable):
            def __init__(self, action, callback, args):
                super().__init__()
                self.action = action
                self.callback = callback
                self.args = args
                self.signals = _LearningSignals()

            @Slot()
            def run(self):
                try:
                    result = self.callback(*self.args)
                    self.signals.finished.emit(result, None)
                except Exception as exc:
                    self.signals.finished.emit(None, exc)

        page = QWidget(parent)
        receiver = _LearningReceiver()
        page.setObjectName("yukiLearningCenter")
        layout = QVBoxLayout(page)

        heading = QLabel("Learning Center")
        heading.setObjectName("learningHeading")
        layout.addWidget(heading)

        explanation = QLabel(
            "Yuki never scans chat history for training. Submit a specific correction "
            "only when you choose. Personalization consent and model-training consent "
            "are separate: learning consent controls few-shot personalization, while "
            "training consent controls SFT dataset export. Approved examples never "
            "change model weights automatically. Turning consent off stops its "
            "corresponding use. Use the deletion control below to permanently remove "
            "all saved pending and approved learning samples. Audit records retain only "
            "hashed identifiers and revocation events, not sample text."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        learning_consent = QCheckBox("Allow explicitly submitted corrections for personalization")
        training_consent = QCheckBox("Allow separately approved examples in training datasets")
        layout.addWidget(learning_consent)
        layout.addWidget(training_consent)

        status = QLabel("Learning is off until you enable it.")
        status.setObjectName("learningStatus")
        status.setTextFormat(Qt.PlainText)
        status.setWordWrap(True)
        layout.addWidget(status)

        summary = QLabel("0 pending corrections · 0 approved examples")
        summary.setTextFormat(Qt.PlainText)
        layout.addWidget(summary)

        refresh = QPushButton("Refresh learning status")
        layout.addWidget(refresh)
        submit = QPushButton("Submit a correction manually")
        submit.setToolTip("Submit only the exact prompt and corrected answer you enter. Yuki never scans chat history.")
        submit.setEnabled(False)
        layout.addWidget(submit)

        pending_heading = QLabel("Corrections awaiting your review")
        layout.addWidget(pending_heading)
        pending = QListWidget()
        pending.setObjectName("learningPendingCorrections")
        layout.addWidget(pending, 1)

        review_heading = QLabel("Sample preview")
        layout.addWidget(review_heading)
        preview = QPlainTextEdit()
        preview.setObjectName("learningCorrectionPreview")
        preview.setReadOnly(True)
        preview.setPlaceholderText("Select a correction to inspect its messages.")
        layout.addWidget(preview, 2)

        actions = QHBoxLayout()
        approve = QPushButton("Approve for training")
        reject = QPushButton("Reject correction")
        approve.setEnabled(False)
        reject.setEnabled(False)
        actions.addWidget(approve)
        actions.addWidget(reject)
        layout.addLayout(actions)

        delete_all = QPushButton("Delete all saved learning data")
        layout.addWidget(delete_all)

        selected_id = {"value": None}
        state_cache = {"learning": False, "training": False}

        class Presenter:
            def set_status(self, message: str):
                status.setText(_plain(message, 500) or "Learning status unavailable.")

            def render_state(self, state: dict):
                if not isinstance(state, dict):
                    raise ValueError("Learning state must be an object.")
                learning = state.get("learning_consent")
                training = state.get("training_consent")
                rows = state.get("pending", [])
                approved_count = state.get("approved_count", 0)
                retained_pending_count = state.get("retained_pending_count", len(rows))
                if type(learning) is not bool or type(training) is not bool:
                    raise ValueError("Learning consent state is invalid.")
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError("Pending correction list exceeds its limit.")
                if type(approved_count) is not int or not 0 <= approved_count <= 10_000:
                    raise ValueError("Approved example count is invalid.")
                if type(retained_pending_count) is not int or not 0 <= retained_pending_count <= 100:
                    raise ValueError("Retained pending count is invalid.")
                if not learning and rows:
                    raise ValueError("Pending corrections cannot be shown without learning consent.")
                submit.setEnabled(learning and on_submit is not None)
                normalized = []
                seen = set()
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError("Pending correction entry is invalid.")
                    proposal_id = row.get("proposal_id")
                    task_type = row.get("task_type")
                    message_count = row.get("message_count")
                    if (
                        not isinstance(proposal_id, str)
                        or not _SAFE_ID.fullmatch(proposal_id)
                        or proposal_id in seen
                        or not isinstance(task_type, str)
                        or not _SAFE_TASK.fullmatch(task_type)
                        or not (isinstance(message_count, str) or type(message_count) is int)
                    ):
                        raise ValueError("Pending correction metadata is invalid.")
                    seen.add(proposal_id)
                    normalized.append((proposal_id, task_type, str(message_count)))
                state_cache["learning"] = learning
                state_cache["training"] = training
                for checkbox, value in (
                    (learning_consent, learning), (training_consent, training),
                ):
                    checkbox.blockSignals(True)
                    checkbox.setChecked(value)
                    checkbox.blockSignals(False)
                pending.clear()
                for proposal_id, task_type, message_count in normalized:
                    item = QListWidgetItem(
                        f"{task_type} · {message_count} messages · {proposal_id[:8]}"
                    )
                    item.setData(Qt.UserRole, proposal_id)
                    pending.addItem(item)
                if learning:
                    summary.setText(
                        f"{len(normalized)} pending corrections · {approved_count} approved examples stored"
                    )
                else:
                    summary.setText(
                        f"{retained_pending_count} pending corrections hidden by consent · "
                        f"{approved_count} approved examples stored"
                    )
                saved_status = state.get("status")
                if isinstance(saved_status, str) and saved_status.strip():
                    status.setText(_plain(saved_status, 500))
                elif not learning:
                    status.setText("Personalization is off. No correction samples are available for review.")
                elif not training:
                    status.setText(
                        "Few-shot personalization is enabled; SFT consent is off. "
                        "Existing approved examples may guide responses, but new approvals and exports are disabled."
                    )
                else:
                    status.setText("Both consent controls are enabled. Review each example before approval.")
                selected_id["value"] = None
                preview.clear()
                approve.setEnabled(False)
                reject.setEnabled(False)

            def render_review(self, proposal_id: str, item: dict | None):
                if item is None:
                    selected_id["value"] = None
                    preview.clear()
                    approve.setEnabled(False)
                    reject.setEnabled(False)
                    return
                if not isinstance(item, dict) or item.get("proposal_id") != proposal_id:
                    raise ValueError("Correction preview does not match the selected proposal.")
                messages = item.get("messages")
                if not isinstance(messages, list) or not 2 <= len(messages) <= 32:
                    raise ValueError("Correction preview messages are invalid.")
                blocks = []
                total = 0
                for message in messages:
                    if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
                        raise ValueError("Correction preview message role is invalid.")
                    text = message.get("content")
                    if not isinstance(text, str) or not text.strip() or len(text) > 16_000:
                        raise ValueError("Correction preview message content is invalid.")
                    total += len(text)
                    if total > 48_000:
                        raise ValueError("Correction preview exceeds its size limit.")
                    blocks.append(f"{message['role'].upper()}:\n{text}")
                # Render only message content. Source references and hidden metadata
                # are deliberately not accepted by this view.
                preview.setPlainText("\n\n".join(blocks))
                selected_id["value"] = proposal_id
                approve.setEnabled(state_cache["learning"] and state_cache["training"])
                reject.setEnabled(state_cache["learning"])

        presenter = Presenter()

        pool = QThreadPool.globalInstance()
        busy = {"value": False, "worker": None}
        disposed = {"value": False}

        def mark_disposed(_object=None):
            disposed["value"] = True
            if not busy["value"]:
                receiver.deleteLater()

        page.destroyed.connect(mark_disposed)

        def set_controls_enabled(enabled: bool):
            for control in (
                learning_consent, training_consent, refresh, submit, pending,
                approve, reject, delete_all,
            ):
                control.setEnabled(enabled)
            if enabled:
                submit.setEnabled(state_cache["learning"] and on_submit is not None)
                approve.setEnabled(
                    selected_id["value"] is not None
                    and state_cache["learning"] and state_cache["training"]
                )
                reject.setEnabled(
                    selected_id["value"] is not None and state_cache["learning"]
                )

        def run_async(action, callback, args=(), *, on_success=None, on_error=None):
            if busy["value"]:
                return False
            busy["value"] = True
            set_controls_enabled(False)
            presenter.set_status("Working securely…")
            worker = _LearningWorker(action, callback, args)
            worker.receiver = receiver
            busy["worker"] = worker

            def completed(result, error):
                busy["value"] = False
                busy["worker"] = None
                if disposed["value"]:
                    worker.receiver = None
                    receiver.deleteLater()
                    return
                set_controls_enabled(True)
                if error is not None:
                    if on_error is not None:
                        on_error(error)
                    else:
                        presenter.set_status("The learning operation could not be completed safely.")
                    return
                if on_success is not None:
                    on_success(result)

            receiver.completion = completed
            worker.signals.finished.connect(receiver.dispatch)
            pool.start(worker)
            return True

        def refresh_state(_checked=False, *, after_refresh=None):
            def refreshed(state):
                try:
                    presenter.render_state(state)
                    set_controls_enabled(True)
                    if after_refresh is not None:
                        after_refresh()
                except Exception:
                    presenter.set_status("Could not safely load learning status. Personalization is paused.")
                    state_cache["learning"] = False
                    state_cache["training"] = False
                    pending.clear()
                    preview.clear()
                    submit.setEnabled(False)
                    approve.setEnabled(False)
                    reject.setEnabled(False)

            def failed(_error):
                presenter.set_status("Could not safely load learning status. Personalization is paused.")
                state_cache["learning"] = False
                state_cache["training"] = False
                pending.clear()
                preview.clear()
                submit.setEnabled(False)
                approve.setEnabled(False)
                reject.setEnabled(False)

            run_async("refresh", on_refresh, on_success=refreshed, on_error=failed)

        def refresh_with_status(message: str):
            def refreshed(state):
                try:
                    presenter.render_state(state)
                    set_controls_enabled(True)
                    presenter.set_status(message)
                except Exception:
                    presenter.set_status(
                        "Could not safely refresh learning status. Personalization is paused."
                    )

            run_async(
                "refresh", on_refresh,
                on_success=refreshed,
                on_error=lambda _error: presenter.set_status(
                    "The action completed, but learning status could not be refreshed."
                ),
            )

        def consent_changed(purpose: str, enabled: bool, checkbox):
            previous = state_cache[purpose]
            if enabled:
                answer = QMessageBox.question(
                    page,
                    "Enable " + purpose + " consent",
                    (
                        "Yuki will retain only corrections you explicitly submit. "
                        "It will not scan chat history. You can turn this off later."
                        if purpose == "learning"
                        else "Separately approved examples may be used in future SFT dataset exports. "
                             "No remote upload or model training starts automatically."
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    checkbox.blockSignals(True)
                    checkbox.setChecked(previous)
                    checkbox.blockSignals(False)
                    return

            def saved(_result):
                refresh_state()

            def failed(_error):
                checkbox.blockSignals(True)
                checkbox.setChecked(previous)
                checkbox.blockSignals(False)
                presenter.set_status("The consent change could not be saved. The previous setting remains.")

            run_async(
                "consent", on_consent, (purpose, enabled),
                on_success=saved, on_error=failed,
            )

        def selection_changed(item, _previous=None):
            if item is None:
                presenter.render_review("", None)
                return
            proposal_id = item.data(Qt.UserRole)
            if not isinstance(proposal_id, str) or not _SAFE_ID.fullmatch(proposal_id):
                presenter.render_review("", None)
                return

            selected_id["value"] = proposal_id
            preview.clear()
            approve.setEnabled(False)
            reject.setEnabled(False)

            def reviewed(result):
                if selected_id["value"] != proposal_id:
                    return
                try:
                    presenter.render_review(proposal_id, result)
                    presenter.set_status("Correction loaded for review.")
                except Exception:
                    presenter.render_review("", None)
                    presenter.set_status("The correction preview could not be loaded safely.")

            run_async(
                "review", on_review, (proposal_id,),
                on_success=reviewed,
                on_error=lambda _error: presenter.set_status(
                    "The correction preview could not be loaded safely."
                ),
            )

        def approve_selected(_checked=False):
            proposal_id = selected_id["value"]
            if not proposal_id or not (state_cache["learning"] and state_cache["training"]):
                return
            answer = QMessageBox.question(
                page,
                "Approve correction",
                "Approve this exact sample for the training dataset? "
                "It may also be used as an approved few-shot reference. "
                "This does not start model training or change model weights.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

            def approved(_result):
                refresh_with_status("Correction approved and recorded.")

            run_async(
                "approve", on_approve, (proposal_id,),
                on_success=approved,
                on_error=lambda _error: presenter.set_status(
                    "Approval could not be committed. The sample remains unapproved."
                ),
            )

        def reject_selected(_checked=False):
            proposal_id = selected_id["value"]
            if not proposal_id:
                return
            answer = QMessageBox.question(
                page, "Reject correction",
                "Permanently discard this pending correction from the learning queue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

            def rejected(_result):
                refresh_with_status("Pending correction rejected.")

            run_async(
                "reject", on_reject, (proposal_id,),
                on_success=rejected,
                on_error=lambda _error: presenter.set_status(
                    "The correction could not be removed from encrypted storage."
                ),
            )

        def submit_correction(_checked=False):
            if on_submit is None or not state_cache["learning"]:
                return
            dialog = QDialog(page)
            dialog.setWindowTitle("Submit a correction")
            dialog_layout = QVBoxLayout(dialog)
            notice = QLabel(
                "Enter only the exact example you want Yuki to learn from. "
                "This is saved locally in encrypted storage after you submit. "
                "Do not include passwords, private keys, payment details, or confidential information. "
                "No chat history is scanned or copied automatically."
            )
            notice.setWordWrap(True)
            dialog_layout.addWidget(notice)
            fields = QFormLayout()
            user_message = QPlainTextEdit()
            user_message.setPlaceholderText("The exact instruction or question you gave Yuki")
            user_message.setMaximumHeight(110)
            assistant_message = QPlainTextEdit()
            assistant_message.setPlaceholderText("The corrected answer you want Yuki to use as an example")
            assistant_message.setMaximumHeight(140)
            task_type = QLineEdit("general")
            task_type.setMaxLength(64)
            fields.addRow("Your message", user_message)
            fields.addRow("Corrected answer", assistant_message)
            fields.addRow("Task type", task_type)
            dialog_layout.addLayout(fields)
            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            )
            buttons.button(QDialogButtonBox.Ok).setText("Submit encrypted sample")
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            dialog_layout.addWidget(buttons)
            if dialog.exec() != QDialog.Accepted:
                return
            user_text = user_message.toPlainText().strip()
            assistant_text = assistant_message.toPlainText().strip()
            task = task_type.text().strip().lower()
            if (
                not user_text or len(user_text) > 20_000
                or not assistant_text or len(assistant_text) > 20_000
                or not _SAFE_TASK.fullmatch(task)
            ):
                presenter.set_status(
                    "Enter a valid task type and non-empty messages (each up to 20,000 characters)."
                )
                return
            answer = QMessageBox.question(
                page,
                "Confirm correction submission",
                "Save these exact two messages as a pending learning sample? "
                "The sample will not be used until you review and approve it. "
                "It will not start model training.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

            messages = [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]

            def submitted(_proposal_id):
                refresh_with_status("Correction submitted for your review.")

            run_async(
                "submit", on_submit,
                (messages, "desktop-learning-center", task),
                on_success=submitted,
                on_error=lambda _error: presenter.set_status(
                    "The correction was not saved. Check consent and secure storage."
                ),
            )

        def delete_all_data(_checked=False):
            answer = QMessageBox.question(
                page,
                "Delete all saved learning data",
                "Permanently delete every pending correction and approved example "
                "from this profile? This cannot be undone. Minimal hashed audit "
                "metadata and approval-revocation events will remain.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

            def deleted(counts):
                if (
                    not isinstance(counts, dict)
                    or type(counts.get("pending")) is not int
                    or type(counts.get("approved")) is not int
                    or not 0 <= counts["pending"] <= 100
                    or not 0 <= counts["approved"] <= 10_000
                ):
                    presenter.set_status("Deletion result was invalid; verify storage before retrying.")
                    return
                refresh_with_status(
                    f"Deleted {counts['pending']} pending corrections and "
                    f"{counts['approved']} approved examples."
                )

            run_async(
                "delete", on_delete_all,
                on_success=deleted,
                on_error=lambda _error: presenter.set_status(
                    "Deletion could not be completed safely. Stored learning data was retained."
                ),
            )

        learning_consent.toggled.connect(
            lambda enabled: consent_changed("learning", enabled, learning_consent)
        )
        training_consent.toggled.connect(
            lambda enabled: consent_changed("training", enabled, training_consent)
        )
        refresh.clicked.connect(refresh_state)
        submit.clicked.connect(submit_correction)
        pending.currentItemChanged.connect(selection_changed)
        approve.clicked.connect(approve_selected)
        reject.clicked.connect(reject_selected)
        delete_all.clicked.connect(delete_all_data)

        page.learning_presenter = presenter
        page.learning_controls = {
            "learning_consent": learning_consent,
            "training_consent": training_consent,
            "status": status,
            "summary": summary,
            "refresh": refresh,
            "submit": submit,
            "pending": pending,
            "preview": preview,
            "approve": approve,
            "reject": reject,
            "delete_all": delete_all,
        }
        refresh_state()
        return page


__all__ = ["LearningCenterPanel"]
