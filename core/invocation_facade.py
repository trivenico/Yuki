"""Single controlled facade for manifest-dispatched skill execution.

Legacy in-process skills are intentionally not accepted here. Callers must supply
an explicit review callback; this facade never auto-approves actions.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


class InvocationFacade:
    def __init__(self, dispatcher):
        if dispatcher is None or not callable(getattr(dispatcher, "request_approval", None)) or not callable(getattr(dispatcher, "execute", None)):
            raise TypeError("A manifest SkillDispatcher is required.")
        self._dispatcher = dispatcher

    def invoke(
        self,
        skill_id: str,
        arguments: dict[str, Any],
        *,
        confirm: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any]:
        if not callable(confirm):
            raise TypeError("An explicit confirmation callback is required.")
        request = self._dispatcher.request_approval(skill_id, deepcopy(arguments))
        # The callback receives review metadata but never the bearer token.
        review = {
            "skill_id": request["skill_id"],
            "risk": request["risk"],
            "arguments": deepcopy(request["arguments"]),
            "payload_sha256": request["payload_sha256"],
            "expires_at": request["expires_at"],
        }
        try:
            decision = confirm(review)
        except Exception:
            self._dispatcher.approval_store.deny(request["approval_id"])
            return {"ok": False, "status": "confirmation_failed"}
        if decision is not True:
            self._dispatcher.approval_store.deny(request["approval_id"])
            return {"ok": False, "status": "denied"}
        try:
            result = self._dispatcher.execute(
                request["skill_id"], deepcopy(request["arguments"]),
                approval_id=request["approval_id"],
            )
        except Exception:
            return {"ok": False, "status": "execution_failed"}
        return result if isinstance(result, dict) else {"ok": False, "status": "invalid_result"}
