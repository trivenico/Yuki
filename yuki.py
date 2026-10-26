"""Yuki: beginner-friendly terminal agent prototype with local safety controls."""

import json
import sqlite3
from pathlib import Path

from yuki_sqlite_store import SQLiteJsonStore

from yuki_agent import YukiAgent
from yuki_learning import record_outcome, suggest_lessons, load_history, clear_history
from yuki_providers import ProviderManager
from yuki_security import SecurityPolicy, load_audit_events, sanitize_text, write_audit_event
from yuki_tasks import load_tasks, add_task, list_tasks, complete_task, delete_task
from yuki_tools import TOOLS, run_tool
from skills.registry import SkillRegistry
from core.guarded_skill_runtime import OneShotSkillApprovals
from core.approved_skill_runtime import ApprovedSkillRuntime
from core.guarded_tool_runtime import GuardedToolRuntime

_INVOCATION_APPROVALS = OneShotSkillApprovals()

MEMORY_FILE = Path(__file__).with_name("yuki_memory.json")
MEMORY_DB_FILE = MEMORY_FILE.with_suffix(".sqlite3")
_MEMORY_KEY = "saved_memory"
MAX_MEMORY_ITEMS = 200
MAX_MEMORY_TEXT = 1000


def _normalize_memory(data):
    if not isinstance(data, list):
        return []
    return [sanitize_text(item, MAX_MEMORY_TEXT) for item in data[-MAX_MEMORY_ITEMS:]
            if isinstance(item, str) and item.strip()]


def _memory_store():
    return SQLiteJsonStore(MEMORY_DB_FILE)


def load_memory():
    """Load SQLite memory, migrating valid legacy JSON without deleting it."""
    try:
        if MEMORY_DB_FILE.exists():
            stored = _memory_store().read_json(_MEMORY_KEY, None)
            if stored is not None:
                return _normalize_memory(stored)
        if not MEMORY_FILE.exists():
            return []
        try:
            legacy = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(legacy, list):
            return []
        safe = _normalize_memory(legacy)
        migrated = _memory_store().update_json(
            _MEMORY_KEY, lambda current: safe if current is None else current, default=None
        )
        return _normalize_memory(migrated)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return []


def save_memory(memory):
    """Replace saved memory transactionally; return False if persistence fails."""
    safe_memory = _normalize_memory(memory)
    try:
        _memory_store().write_json(_MEMORY_KEY, safe_memory)
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False


def _run_audited_action(action, operation, is_success):
    """Require a durable start event before a CLI mutation and record its result."""
    if not write_audit_event(action, "started"):
        return "Operation blocked because audit logging is unavailable."
    try:
        result = operation()
    except Exception:
        if not write_audit_event(action, "failed"):
            return "Operation failed, and audit logging is unavailable."
        return "Operation failed safely."
    outcome = "completed" if is_success(result) else "failed"
    if not write_audit_event(action, outcome):
        return "Operation may have completed, but audit logging failed. Verify the outcome before retrying."
    return result


def add_memory_note(memory, note):
    """Append a note atomically so concurrent sessions do not overwrite each other."""
    safe_note = sanitize_text(note, MAX_MEMORY_TEXT)
    if not safe_note.strip():
        return False
    try:
        store = _memory_store()
        updated = store.update_json(
            _MEMORY_KEY,
            lambda current: (_normalize_memory(current) + [safe_note])[-MAX_MEMORY_ITEMS:],
            default=load_memory(),
        )
        memory[:] = _normalize_memory(updated)
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False


_SKILL_APPROVAL_DB = Path(__file__).with_name('.yuki') / 'skill_approvals.sqlite3'


def _get_skill_approval_store():
    from core.approved_skill_wiring import SkillApprovalStore
    _SKILL_APPROVAL_DB.parent.mkdir(parents=True, exist_ok=True)
    return SkillApprovalStore(_SKILL_APPROVAL_DB)


def show_help():
    print("""
Yuki commands:
  /help                           Show this help
  /status                         Show provider status
  /providers                      List provider adapters and implementation status
  /provider select NAME           Select an implemented provider adapter
  /dashboard                      Show Yuki capability and local-state summary
  /tool NAME [argument]           Run an explicitly allowed tool
  /skill list                     List registered skills
  /skill inspect NAME             Inspect a registered skill's metadata
  /skill approve NAME             Approve one high-risk invocation
  /skill invoke NAME [argument]   Invoke a registered skill through the guard
  /task add DESCRIPTION           Add a task
  /task list                      List tasks
  /task done ID                   Complete a task
  /task delete ID                 Delete a task
  /memory                         Show saved notes
  /memory add TEXT                Save a note locally
  /memory clear                   Clear saved notes
  /learn TASK | OUTCOME | LESSON  Record an outcome
  /lessons TASK                   Find relevant saved lessons
  /learning                       Show recent learning history
  /learning clear                 Clear learning history
  /audit                          Show recent metadata-only audit events
  /quit                           Exit Yuki

The default mock provider makes no network calls. Provider registry entries are not live integrations.
""")


def handle_command(message, memory, tasks, agent, providers, policy=None):
    command = message.strip()
    if not command:
        return ""

    policy = policy or SecurityPolicy(allowed_tools=TOOLS.keys())

    if command in ("/quit", "/exit"):
        return "__QUIT__"
    if command == "/help":
        show_help()
        return ""
    if command == "/status":
        current = providers.current()
        return f"Agent status: {agent.status()} Selected provider: {current.name if current else 'none'}."
    if command == "/provider" or command.startswith("/provider "):
        parts = command.split(maxsplit=2)
        if len(parts) != 3 or parts[1] != "select":
            return "Usage: /provider select NAME"
        def select_provider():
            result = providers.select(parts[2])
            if result.startswith("Selected provider:"):
                adapter = providers.create_selected_adapter()
                if adapter is None:
                    return "Provider selection failed: no adapter is available."
                agent.set_provider(adapter)
            return result
        return _run_audited_action(
            "provider:select", select_provider,
            lambda result: isinstance(result, str) and result.startswith("Selected provider:")
        )
    if command == "/dashboard":
        current = providers.current()
        available_tools = ", ".join(policy.allowed_tools())
        tasks_open = sum(1 for item in tasks if isinstance(item, dict) and not item.get("completed", False))
        return (
            "Yuki dashboard\n"
            f"Provider: {current.name if current else 'none'} ({agent.status()})\n"
            f"Allowed tools: {available_tools or 'none'}\n"
            f"Open tasks: {tasks_open}\n"
            f"Saved notes: {len(memory)}\n"
            f"Learning records: {len(load_history())}\n"
            f"Recent audit events: {len(load_audit_events())}"
        )
    if command == "/providers":
        return "\n".join(
            f'{item["name"]}: {"implemented" if item["implemented"] else "not implemented"}'
            f'{" (selected)" if item["selected"] else ""}; auth metadata: {", ".join(item["auth_methods"])}'
            for item in providers.list_providers()
        )
    if command == "/skill" or command.startswith("/skill "):
        parts = command.split(maxsplit=3)
        try:
            registry = SkillRegistry.defaults(Path.cwd())
            runtime = ApprovedSkillRuntime(
                registry,
                _get_skill_approval_store(),
                invocation_approvals=_INVOCATION_APPROVALS,
                audit=lambda action, outcome: write_audit_event(action, outcome),
            )
        except Exception as error:
            return f"Skill runtime unavailable ({type(error).__name__})."
        if len(parts) == 1 or parts[1] == "list":
            return "\n".join(
                f'{item["name"]} [{item["risk"]}]: {item["description"]}'
                for item in registry.describe()
            ) or "No skills registered."
        if parts[1] == "inspect":
            if len(parts) < 3:
                return "Usage: /skill inspect NAME"
            skill = registry.get(parts[2])
            if skill is None:
                return "Unknown skill."
            try:
                description = str(getattr(skill, "description", "No description provided."))
                risk = str(getattr(skill, "risk", "high")).lower()
                requires_approval = (
                    bool(getattr(skill, "requires_approval", True))
                    or risk not in {"low", "medium", "high", "critical"}
                    or risk in {"high", "critical"}
                )
            except Exception:
                return "Skill metadata is unavailable."
            description = description.replace("\r", " ").replace("\n", " ")[:500]
            return (
                f"Name: {parts[2]}\n"
                f"Description: {description or 'No description provided.'}\n"
                f"Risk: {risk[:32]}\n"
                f"Invocation approval: {'required' if requires_approval else 'not required'}"
            )
        if parts[1] == "approve" and len(parts) >= 3:
            try:
                return _run_audited_action(
                    "skill:approval",
                    lambda: (runtime.approve_invocation(parts[2])
                             or f"One-shot invocation approval granted for {parts[2]}."),
                    lambda result: isinstance(result, str) and result.startswith("One-shot invocation approval granted")
                )
            except ValueError:
                return "Cannot approve an unknown skill."
        if parts[1] == "invoke" and len(parts) >= 3:
            argument = parts[3] if len(parts) == 4 else ""
            result = runtime.invoke(parts[2], argument)
            return result.message if result.ok else f"{result.status}: {result.message}"
        return "Usage: /skill list | approve NAME | invoke NAME [argument]"

    if command == "/tool" or command.startswith("/tool "):
        parts = command.split(maxsplit=2)
        if len(parts) < 2:
            return "Usage: /tool NAME [argument]"
        name = parts[1]
        runtime = GuardedToolRuntime(
            TOOLS,
            policy,
            audit=lambda action, outcome: write_audit_event(action, outcome),
        )
        return runtime.invoke(name, parts[2] if len(parts) > 2 else "")
    if command == "/task" or command.startswith("/task "):
        parts = command.split(maxsplit=2)
        if len(parts) < 2 or parts[1] == "list":
            return list_tasks(tasks)
        if parts[1] == "add":
            return _run_audited_action(
                "task:add",
                lambda: add_task(tasks, parts[2] if len(parts) > 2 else ""),
                lambda result: result == "Task added."
            )
        if parts[1] in ("done", "delete"):
            if len(parts) < 3:
                return f"Usage: /task {parts[1]} ID"
            operation = complete_task if parts[1] == "done" else delete_task
            expected = "Task completed." if parts[1] == "done" else "Task deleted."
            return _run_audited_action(
                "task:" + parts[1], lambda: operation(tasks, parts[2]),
                lambda result: result == expected
            )
        return "Usage: /task add DESCRIPTION | list | done ID | delete ID"
    if command == "/memory":
        return "\n".join(f"{i + 1}. {note}" for i, note in enumerate(memory)) or "No saved memory yet."
    if command.startswith("/memory add "):
        note = sanitize_text(command[len("/memory add "):].strip(), MAX_MEMORY_TEXT)
        if not note:
            return "Please provide a note."
        return _run_audited_action(
            "memory:add",
            lambda: ("Memory saved locally. Avoid storing secrets or sensitive personal data."
                     if add_memory_note(memory, note) else "Could not save memory."),
            lambda result: isinstance(result, str) and result.startswith("Memory saved locally.")
        )
    if command == "/memory clear":
        def clear_saved_memory():
            old = memory[:]
            memory.clear()
            if save_memory(memory):
                return "Saved memory cleared."
            memory.extend(old)
            return "Could not clear memory."
        return _run_audited_action(
            "memory:clear", clear_saved_memory,
            lambda result: result == "Saved memory cleared."
        )
    if command.startswith("/learn "):
        parts = command[len("/learn "):].split("|", 2)
        if len(parts) < 2:
            return "Usage: /learn TASK | OUTCOME | LESSON"
        return _run_audited_action(
            "learning:record",
            lambda: record_outcome(parts[0].strip(), parts[1].strip(),
                                   "Recorded through terminal", parts[2].strip() if len(parts) > 2 else ""),
            lambda result: isinstance(result, str) and result.startswith("Outcome recorded")
        )
    if command.startswith("/lessons"):
        task = command[len("/lessons"):].strip()
        lessons = suggest_lessons(task)
        return "\n".join(f"- {lesson}" for lesson in lessons) if lessons else "No matching lessons saved yet."
    if command == "/learning":
        history = load_history()
        if not history:
            return "No learning history yet."
        return "\n".join(f'{item.get("created_at", "")[:10]} — {item.get("task", "")}: {item.get("outcome", "")}'
                         for item in history[-10:])
    if command == "/learning clear":
        return _run_audited_action(
            "learning:clear", clear_history,
            lambda result: result == "Learning history cleared."
        )
    if command == "/audit":
        events = load_audit_events()[-10:]
        if not events:
            return "No audit events yet."
        return "\n".join(f'{item["timestamp"][:19]} {item["action"]}: {item["outcome"]}' for item in events)

    related = suggest_lessons(command)
    response = agent.ask(command)
    if related:
        response += "\n\nRelevant saved lessons:\n" + "\n".join(f"- {lesson}" for lesson in related)
    return response


def main():
    providers = ProviderManager()
    agent = YukiAgent(provider=providers.create_selected_adapter())
    policy = SecurityPolicy(allowed_tools=TOOLS.keys())
    memory = load_memory()
    tasks = load_tasks()
    print("Yuki terminal prototype. Type /help for commands; /quit to exit.")
    print("Safety note: tools are allow-listed; audit records contain metadata, not command arguments.")
    while True:
        try:
            message = input("\nYou > ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        try:
            response = handle_command(message, memory, tasks, agent, providers, policy)
        except Exception as error:
            response = f"Yuki encountered a recoverable error: {type(error).__name__}."
        if response == "__QUIT__":
            print("Yuki > Goodbye.")
            break
        if response:
            print(f"Yuki > {response}")


if __name__ == "__main__":
    main()
