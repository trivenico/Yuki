"""Deprecated compatibility shim for the unsafe legacy approval store.

This class intentionally cannot grant authorization. Use PermissionGate and its
fresh, action-bound approval flow instead.
"""


class ActionApprovalStore:
    """Fail-closed compatibility shim; never treats an action ID as approval."""

    def __init__(self):
        pass

    def approve(self, action_id: str, approved: bool = True):
        raise RuntimeError(
            "ActionApprovalStore cannot grant approvals; use PermissionGate."
        )

    def is_approved(self, action_id: str) -> bool:
        return False
