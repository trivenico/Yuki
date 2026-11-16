"""Composition seam for the desktop's shared chat and legacy specialist services.

The caller continues to own the provider and specialist service lifecycle. The
returned bridge owns only the authenticated conversation session and delegates
specialist/task/backup operations to the existing authenticated controller.
"""
from __future__ import annotations

from typing import Any, Callable

from core.conversation_store_factory import create_conversation_store
from ui.desktop.shared_chat_controller import SharedDesktopChatController
from ui.desktop.shared_conversation_session import (
    build_authenticated_desktop_conversation,
)
from ui.desktop.shared_desktop_controller import SharedDesktopControllerBridge


def build_shared_desktop_controller(
    config: dict[str, Any],
    *,
    provider: Any,
    identity: Any,
    sessions: Any,
    specialist_controller: Any,
    memory: Any = None,
    store_factory: Callable[[dict[str, Any]], Any] = create_conversation_store,
) -> SharedDesktopControllerBridge:
    """Build a desktop controller with durable chat and preserved specialist APIs.

    Authentication is verified by build_authenticated_desktop_conversation.
    Provider shutdown remains with the outer process lifecycle; the returned
    bridge closes the conversation store/facade and logs out the specialist
    controller exactly once. Register the provider only once with the outer
    lifecycle and do not separately register the returned conversation runtime.
    """
    conversation_session = build_authenticated_desktop_conversation(
        config,
        provider=provider,
        identity=identity,
        sessions=sessions,
        memory=memory,
        store_factory=store_factory,
    )
    try:
        chat = SharedDesktopChatController(conversation_session)
        return SharedDesktopControllerBridge(chat, specialist_controller)
    except Exception:
        # The bridge constructor can reject an incomplete legacy-controller
        # contract; the session bundle must still be closed on every failure.
        conversation_session.close()
        raise
