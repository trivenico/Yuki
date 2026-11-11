"""Local companion composition over Yuki shared conversation runtime.

Companion records and relationship preferences use owner-scoped local SQLite
stores. Memory remains opt-in; legacy JSON state is imported non-destructively.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from companion import DEFAULT_PERSONA, EmotionState, Persona
from companion.identity_manager import IdentityManager
from companion.memory import MemoryStore
from companion.proactive import ReminderQueue
from companion.relationship_settings_store import RelationshipSettingsStore
from companion.state_store import CompanionStateStore
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_application_runtime import (
    ConversationApplicationRuntime,
    build_conversation_application_runtime,
)
from core.local_owner_identity import load_or_create_local_owner_id


class CompanionApplicationRuntime:
    """Own local companion state and the shared durable conversation runtime."""

    def __init__(self, *, application: ConversationApplicationRuntime,
                 persona: Persona, emotion: EmotionState, identities: IdentityManager,
                 reminders: ReminderQueue, memory: MemoryStore | None,
                 state_store: CompanionStateStore,
                 relationship_settings: RelationshipSettingsStore,
                 workspace: Path) -> None:
        self.application = application
        self.conversations = application.conversations
        self.owner_id = application.owner_id
        self.persona = persona
        self.emotion = emotion
        self.identities = identities
        self.reminders = reminders
        self.memory = memory
        self.state_store = state_store
        self.relationship_settings = relationship_settings
        self.workspace = workspace
        self._closed = False

    @property
    def memory_enabled(self) -> bool:
        return self.memory is not None

    def companion_guidance(self) -> str:
        """Return transparent guidance; it is not silently injected into the model loop."""
        return f"{self.persona.system_guidance()} {self.emotion.guidance()}"

    def close(self) -> None:
        if self._closed:
            return
        self.application.close()
        self._closed = True


def build_companion_application_runtime(
    config: dict[str, Any], *, agent_loop: AgentExecutionLoop,
    persona: Persona = DEFAULT_PERSONA,
    store_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> CompanionApplicationRuntime:
    """Compose a local companion with shared chat and transactional local state."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary.")
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop.")
    if not isinstance(persona, Persona):
        raise TypeError("persona must be a Persona.")
    memory_enabled = config.get("companion_memory_enabled", False)
    if type(memory_enabled) is not bool:
        raise ValueError("companion_memory_enabled must be a boolean.")
    workspace_value = config.get("workspace")
    if not isinstance(workspace_value, (str, Path)) or not str(workspace_value).strip():
        raise ValueError("workspace must be configured for the local companion.")
    workspace = Path(workspace_value).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    if not workspace.is_dir():
        raise ValueError("workspace must resolve to a directory.")
    owner_id = load_or_create_local_owner_id(workspace)
    state_dir = workspace / "companion"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_store = CompanionStateStore(state_dir / "state.sqlite3", owner_id=owner_id)
    relationship_settings = RelationshipSettingsStore(
        state_dir / "relationship.sqlite3", owner_id=owner_id,
    )

    # Import each old JSON file once. Sources remain intact for rollback/recovery.
    state_store.import_legacy_json("identity", state_dir / "identities.json")
    state_store.import_legacy_json("reminder", state_dir / "reminders.json")
    if memory_enabled:
        state_store.import_legacy_json("memory", state_dir / "memory.json")

    kwargs = {} if store_factory is None else {"store_factory": store_factory}
    application = build_conversation_application_runtime(
        config, agent_loop=agent_loop, owner_id=owner_id, **kwargs,
    )
    try:
        return CompanionApplicationRuntime(
            application=application, persona=persona, emotion=EmotionState(),
            identities=IdentityManager(state_store=state_store),
            reminders=ReminderQueue(state_store=state_store),
            memory=MemoryStore(state_store=state_store) if memory_enabled else None,
            state_store=state_store,
            relationship_settings=relationship_settings,
            workspace=workspace,
        )
    except Exception:
        application.close()
        raise
