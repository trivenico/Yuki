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

    No store, model, background worker, or audit log is created here. The
    authenticated desktop owner supplies callbacks and owns their execution
    lifecycle. Approval is impossible unless separate training consent is on.
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
    ):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
            QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
        )

        callbacks = (on_consent, on_refresh, on_review, on_approve, on_reject)
        if not all(callable(callback) for callback in callbacks):
            raise TypeError("Learning Center callbacks must be callable.")

        page = QWidget(parent)
        page.setObjectName("yukiLearningCenter")
        layout = QVBoxLayout(page)

        heading = QLabel("Learning Center")
        heading.setObjectName("learningHeading")
        layout.addWidget(heading)

        explanation = QLabel(
            "Yuki never scans chat history for training. Submit a specific correction "
            "only when you choose. Personalization consent and model-training consent "
            "are separate. Approved examples may guide responses, but they do not "
            "change model weights automatically. Turning consent off stops future use; "
            "it does not delete samples already saved in encrypted storage. Data-deletion "
            "controls are not included in this panel yet."
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
                if type(learning) is not bool or type(training) is not bool:
                    raise ValueError("Learning consent state is invalid.")
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError("Pending correction list exceeds its limit.")
                if type(approved_count) is not int or not 0 <= approved_count <= 10_000:
                    raise ValueError("Approved example count is invalid.")
                if not learning and rows:
                    raise ValueError("Pending corrections cannot be shown without learning consent.")
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
                summary.setText(
                    f"{len(normalized)} pending corrections · {approved_count} approved examples"
                )
                saved_status = state.get("status")
                if isinstance(saved_status, str) and saved_status.strip():
                    status.setText(_plain(saved_status, 500))
                elif not learning:
                    status.setText("Personalization is off. No correction samples are available for review.")
                elif not training:
                    status.setText("Personalization is enabled; training consent is off. Approve is disabled.")
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
                approve.setEnabled(state_cache["training"])
                reject.setEnabled(state_cache["learning"])

        presenter = Presenter()

        def refresh_state():
            try:
                presenter.render_state(on_refresh())
            except Exception:
                presenter.set_status("Could not safely load learning status. Personalization is paused.")
                pending.clear()
                preview.clear()
                approve.setEnabled(False)
                reject.setEnabled(False)

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
            try:
                on_consent(purpose, enabled)
                refresh_state()
            except Exception:
                checkbox.blockSignals(True)
                checkbox.setChecked(previous)
                checkbox.blockSignals(False)
                presenter.set_status("The consent change could not be saved. The previous setting remains.")

        def selection_changed(item):
            if item is None:
                presenter.render_review("", None)
                return
            proposal_id = item.data(Qt.UserRole)
            if not isinstance(proposal_id, str) or not _SAFE_ID.fullmatch(proposal_id):
                presenter.render_review("", None)
                return
            try:
                presenter.render_review(proposal_id, on_review(proposal_id))
            except Exception:
                presenter.render_review("", None)
                presenter.set_status("The correction preview could not be loaded safely.")

        def approve_selected():
            proposal_id = selected_id["value"]
            if not proposal_id or not state_cache["training"]:
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
            try:
                on_approve(proposal_id)
                refresh_state()
                presenter.set_status("Correction approved and recorded.")
            except Exception:
                presenter.set_status("Approval could not be committed. The sample remains unapproved.")

        def reject_selected():
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
            try:
                on_reject(proposal_id)
                refresh_state()
                presenter.set_status("Pending correction rejected.")
            except Exception:
                presenter.set_status("The correction could not be removed from encrypted storage.")

        learning_consent.toggled.connect(
            lambda enabled: consent_changed("learning", enabled, learning_consent)
        )
        training_consent.toggled.connect(
            lambda enabled: consent_changed("training", enabled, training_consent)
        )
        refresh.clicked.connect(refresh_state)
        pending.currentItemChanged.connect(selection_changed)
        approve.clicked.connect(approve_selected)
        reject.clicked.connect(reject_selected)

        page.learning_presenter = presenter
        page.learning_controls = {
            "learning_consent": learning_consent,
            "training_consent": training_consent,
            "status": status,
            "summary": summary,
            "refresh": refresh,
            "pending": pending,
            "preview": preview,
            "approve": approve,
            "reject": reject,
        }
        refresh_state()
        return page


__all__ = ["LearningCenterPanel"]
