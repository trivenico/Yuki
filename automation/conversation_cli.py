"""Persistent local terminal chat over the shared conversation runtime."""
from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from typing import Callable, TextIO
import sys

from automation.agent_sessions import AgentSessionStore
from automation.conversation_runtime import ConversationGenerationError, ConversationRuntime
from automation.conversation_service import ConversationService

_LOCAL_ACTOR = "yuki-local-cli"
_MAX_INPUT_CHARS = 32_000


def run_interactive_chat(
    provider_manager,
    *,
    database_path: str | Path | None = None,
    actor_id: str = _LOCAL_ACTOR,
    input_fn: Callable[[str], str] = input,
    output: TextIO | None = None,
    allow_cloud: bool = False,
) -> str:
    """Run a persistent local chat; cloud routing must be explicitly enabled by caller."""
    if not isinstance(allow_cloud, bool):
        raise TypeError("allow_cloud must be a boolean")
    if output is None:
        output = sys.stdout
    if database_path is None:
        database_path = Path(os.environ.get("YUKI_DATA_DIR", Path.home() / ".yuki")) / "conversations.sqlite3"
    path = Path(database_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    sessions = AgentSessionStore(
        path, authorize=lambda actor, action, resource: actor == actor_id,
    )
    conversations = ConversationService(sessions)
    runtime = ConversationRuntime(conversations)
    active = [item for item in conversations.list_sessions(actor_id=actor_id, limit=50)
              if item.state == "active"]
    if active:
        session = active[0]
        print(f"Resuming conversation: {session.title} ({session.session_id})", file=output)
    else:
        session = conversations.start(
            actor_id=actor_id, idempotency_key="cli-session:" + os.urandom(16).hex(),
            title="Yuki CLI conversation", agent_id="general",
        )
        print(f"Started conversation: {session.session_id}", file=output)
    print("Local chat ready. /back returns to Yuki; /cancel interrupts generation.", file=output)
    while True:
        try:
            message = input_fn("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nLeaving chat.", file=output)
            return session.session_id
        if not message:
            continue
        if message in {"/back", "/exit"}:
            return session.session_id
        if message == "/cancel":
            print("Yuki > Send /cancel while streaming with a connected UI to interrupt; current CLI input is synchronous.", file=output)
            continue
        if len(message) > _MAX_INPUT_CHARS:
            print("Yuki > Message exceeds the 32,000-character limit.", file=output)
            continue
        key = "cli-turn:" + os.urandom(16).hex()
        try:
            events = runtime.respond_stream_with_manager(
                actor_id=actor_id, session_id=session.session_id,
                idempotency_key=key, user_message=message,
                provider_manager=provider_manager, allow_cloud=allow_cloud,
            )
            print("Yuki > ", end="", file=output, flush=True)
            finished = False
            for event in events:
                if event.type == "delta":
                    print(event.content, end="", file=output, flush=True)
                elif event.type == "interrupted":
                    print("\n[Generation interrupted]", file=output)
                    finished = True
                elif event.type == "completed":
                    print(file=output)
                    finished = True
            if not finished:
                print("\n[Generation ended without completion]", file=output)
        except ConversationGenerationError:
            print("\nYuki > The selected provider failed. No partial answer was saved.", file=output)
        except (ValueError, TypeError, RuntimeError, PermissionError, KeyError) as exc:
            print(f"\nYuki > Chat request rejected ({type(exc).__name__}).", file=output)
    # The runtime owns durable history; returning the ID lets callers reopen it.
