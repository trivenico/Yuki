"""Desktop factory for encrypted, profile-scoped learning persistence."""
from __future__ import annotations

from typing import Any

from core.approval_audit import SQLiteApprovalAuditLog
from core.portable_paths import desktop_data_root
from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account
from training.continuous_learning import ContinuousLearningCoordinator
from training.learning_authorization import LearningAuthorizationLedger
from training.secure_learning_store import EncryptedLearningStore
from ui.desktop.storage import prepare_private_desktop_root


def create_desktop_learning_store(keyring_module: Any) -> EncryptedLearningStore:
    """Open encrypted learning state under the authenticated desktop profile.

    The store uses a separate OS-keyring service from companion memory, while
    sharing the stable profile account identity. It never falls back to
    plaintext or the process working directory.
    """
    if not callable(getattr(keyring_module, "get_keyring", None)):
        raise TypeError("A trusted OS keyring module is required.")
    root = prepare_private_desktop_root(desktop_data_root())
    profile_id = load_or_create_profile_id(root)
    store = EncryptedLearningStore(
        root / "learning.enc.json",
        keyring_module=keyring_module,
        keyring_account=profile_memory_keyring_account(profile_id),
    )
    store.read()  # Authenticate existing state before exposing it to the runtime.
    return store

def create_desktop_learning_coordinator(
    keyring_module: Any, *, actor_id: str, memory: Any = None,
) -> tuple[ContinuousLearningCoordinator, LearningAuthorizationLedger]:
    """Create an actor-bound coordinator and durable local consent/audit ledger.

    Call only after authentication and pass the actor ID from the trusted
    desktop session object. The returned ledger records explicit consent and
    per-proposal reviewer decisions; callers must not derive actor_id from UI
    text. Production startup still owns when this pair is created/injected.
    """
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
        raise ValueError("actor_id must be bounded authenticated identity text.")
    store = create_desktop_learning_store(keyring_module)
    root = prepare_private_desktop_root(desktop_data_root())
    audit = SQLiteApprovalAuditLog(str(root / "learning-audit.sqlite3"))
    ledger = LearningAuthorizationLedger(audit, actor_id=actor_id)
    coordinator = ContinuousLearningCoordinator(
        memory=memory,
        store=store,
        consent_verifier=ledger.has_consent,
        approval_verifier=ledger.verify_example_approval,
        authorization_snapshot=ledger.authorization_snapshot,
    )
    return coordinator, ledger




def build_learning_center_callbacks(
    coordinator: ContinuousLearningCoordinator,
    ledger: LearningAuthorizationLedger,
) -> dict[str, Any]:
    """Bind Learning Center actions to one actor-bound coordinator/ledger pair."""
    if not isinstance(coordinator, ContinuousLearningCoordinator):
        raise TypeError("coordinator must be a ContinuousLearningCoordinator.")
    if not isinstance(ledger, LearningAuthorizationLedger):
        raise TypeError("ledger must be a LearningAuthorizationLedger.")

    def on_consent(purpose: str, enabled: bool) -> None:
        ledger.record_consent(purpose, enabled)

    def on_refresh() -> dict:
        return coordinator.learning_center_state()

    def on_review(proposal_id: str) -> dict | None:
        return coordinator.correction_for_review(proposal_id)

    def on_approve(proposal_id: str) -> None:
        # The selected sample must still be reviewable and both consent scopes
        # must remain active at the moment the user confirms approval.
        if not ledger.has_consent("learning") or not ledger.has_consent("training"):
            raise RuntimeError("Both learning and training consent must be active.")
        if coordinator.correction_for_review(proposal_id) is None:
            raise RuntimeError("The pending correction is no longer available for review.")
        # Append the approval while holding the encrypted store's
        # cross-process transaction lock, after confirming the pending sample
        # still exists. This serializes approval against delete-all.
        try:
            coordinator.approve_correction(
                proposal_id,
                training_consent=True,
                approved_by=ledger.actor_id,
                approval_recorder=ledger.record_example_approval,
            )
        except Exception:
            # If the encrypted commit fails or consent is revoked during the
            # operation, append a denial so a later re-consent cannot revive
            # this uncommitted approval.
            ledger.record_example_approval(proposal_id, False)
            raise

    def on_delete_all() -> dict[str, int]:
        # Deletion is always available, including after consent is revoked.
        # Each prior approval is revoked in the same serialized store
        # transaction before its encrypted example is removed.
        return coordinator.delete_all_learning_data(
            revoke_approval=lambda proposal_id: ledger.record_example_approval(
                proposal_id, False,
            ),
        )

    def on_reject(proposal_id: str) -> None:
        ledger.record_example_approval(proposal_id, False)
        if not coordinator.reject_correction(proposal_id):
            raise RuntimeError("The pending correction was not found.")

    return {
        "on_consent": on_consent,
        "on_refresh": on_refresh,
        "on_review": on_review,
        "on_approve": on_approve,
        "on_reject": on_reject,
        "on_delete_all": on_delete_all,
    }


__all__ = [
    "build_learning_center_callbacks",
    "create_desktop_learning_coordinator",
    "create_desktop_learning_store",
]
