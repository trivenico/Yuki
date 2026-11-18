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
    )
    return coordinator, ledger



__all__ = ["create_desktop_learning_store", "create_desktop_learning_coordinator"]
