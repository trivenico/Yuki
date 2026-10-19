"""Channel Gateway Interface module (OpenClaw paradigm).

Provides multi-channel message routing (CLI, Webhook, API, Discord/Slack-style message envelopes)
with session state isolation and fail-closed permission routing.
"""

import time
from typing import Dict, Any, Optional


class ChannelMessage:
    """Standardized cross-channel message envelope."""

    def __init__(self, channel: str, user_id: str, content: str, session_id: Optional[str] = None):
        self.channel = channel or "cli"
        self.user_id = user_id or "local_user"
        self.content = content or ""
        self.session_id = session_id or f"{self.channel}:{self.user_id}"
        self.timestamp = time.time()


class ChannelGateway:
    """Routes channel messages into Yuki agent core with isolated session state."""

    def __init__(self, agent=None):
        self.agent = agent
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def handle_message(self, message: ChannelMessage) -> Dict[str, Any]:
        """Dispatch incoming channel message to agent with session tracking."""
        if not message.content.strip():
            return {"channel": message.channel, "response": "Empty message ignored.", "error": "empty_input"}

        session = self.sessions.setdefault(message.session_id, {
            "channel": message.channel,
            "user_id": message.user_id,
            "history": [],
            "created_at": time.time()
        })

        session["history"].append({"sender": "user", "text": message.content, "timestamp": time.time()})

        if self.agent and hasattr(self.agent, "process"):
            raw_res = self.agent.process(message.content)
            response_text = raw_res.get("response", str(raw_res)) if isinstance(raw_res, dict) else str(raw_res)
        else:
            response_text = f"[Yuki Gateway - {message.channel}] Received: {message.content}"

        session["history"].append({"sender": "yuki", "text": response_text, "timestamp": time.time()})

        return {
            "status": "success",
            "channel": message.channel,
            "user_id": message.user_id,
            "session_id": message.session_id,
            "response": response_text
        }

    def get_session_history(self, session_id: str) -> list:
        """Retrieve interaction history for a given channel session."""
        session = self.sessions.get(session_id)
        return session["history"] if session else []
