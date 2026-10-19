"""In-memory or persistent store for action approval decisions."""


class ActionApprovalStore:
    def __init__(self):
        self._approvals = {}

    def approve(self, action_id: str, approved: bool = True):
        self._approvals[action_id] = approved

    def is_approved(self, action_id: str) -> bool:
        return self._approvals.get(action_id, False)
