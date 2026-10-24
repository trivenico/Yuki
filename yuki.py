"""Yuki: beginner-friendly terminal agent prototype with local safety controls."""

import json
from pathlib import Path

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
MAX_MEMORY_ITEMS = 200
MAX_MEMORY_TEXT = 1000


def load_memory():
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [sanitize_text(item, MAX_MEMORY_TEXT) for item in data[-MAX_MEMORY_ITEMS:]
            if isinstance(item, str) and item.strip()]


def save_memory(memory):
    safe_memory = [sanitize_text(item, MAX_MEMORY_TEXT) for item in memory[-MAX_MEMORY_ITEMS:]
                   if isinstance(item, str) and item.strip()]
    temp = MEMORY_FILE.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(safe_memory, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(MEMORY_FILE)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
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
        result = providers.select(parts[2])
        if result.startswith("Selected provider:"):
            try:
                adapter = providers.create_selected_adapter()
                if adapter is None:
                    return "Provider selection failed: no adapter is available."
                agent.set_provider(adapter)
            except Exception:
                return "Provider selection failed. The previous provider remains active."
        write_audit_event("provider:select", "completed" if result.startswith("Selected provider:") else "rejected")
        return result
    if command == "/dashboard":
        current = providers.current()
        available_tools = ", ".join(policy.allowed_tools())
        tasks_open = sum(1 for item in tasks if isinstance(item, dict) and not item.get("completed", False))
        return (
            "Yuki dashboard\\n"
            f"Provider: {current.name if current else 'none'} ({agent.status()})\\n"
            f"Allowed tools: {available_tools or 'none'}\\n"
            f"Open tasks: {tasks_open}\\n"
            f"Saved notes: {len(memory)}\\n"
            f"Learning records: {len(load_history())}\\n"
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
            return "\\n".join(
                f'{item["name"]} [{item["risk"]}]: {item["description"]}'
                for item in registry.describe()
            ) or "No skills registered."
        if parts[1] == "approve" and len(parts) >= 3:
            try:
                runtime.approve_invocation(parts[2])
                write_audit_event("skill:approval", "granted")
                return f"One-shot invocation approval granted for {parts[2]}."
            except ValueError:
                write_audit_event("skill:approval", "rejected")
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
            result = add_task(tasks, parts[2] if len(parts) > 2 else "")
            write_audit_event("task:add", "completed" if result == "Task added." else "failed")
            return result
        if parts[1] in ("done", "delete"):
            if len(parts) < 3:
                return f"Usage: /task {parts[1]} ID"
            result = complete_task(tasks, parts[2]) if parts[1] == "done" else delete_task(tasks, parts[2])
            write_audit_event("task:" + parts[1], "completed" if result in ("Task completed.", "Task deleted.") else "failed")
            return result
        return "Usage: /task add DESCRIPTION | list | done ID | delete ID"
    if command == "/memory":
        return "\n".join(f"{i + 1}. {note}" for i, note in enumerate(memory)) or "No saved memory yet."
    if command.startswith("/memory add "):
        note = sanitize_text(command[len("/memory add "):].strip(), MAX_MEMORY_TEXT)
        if not note:
            return "Please provide a note."
        memory.append(note[:MAX_MEMORY_TEXT])
        if len(memory) > MAX_MEMORY_ITEMS:
            del memory[:-MAX_MEMORY_ITEMS]
        if save_memory(memory):
            write_audit_event("memory:add", "completed")
            return "Memory saved locally. Avoid storing secrets or sensitive personal data."
        memory.pop()
        return "Could not save memory."
    if command == "/memory clear":
        old = memory[:]
        memory.clear()
        if save_memory(memory):
            write_audit_event("memory:clear", "completed")
            return "Saved memory cleared."
        memory.extend(old)
        return "Could not clear memory."
    if command.startswith("/learn "):
        parts = command[len("/learn "):].split("|", 2)
        if len(parts) < 2:
            return "Usage: /learn TASK | OUTCOME | LESSON"
        result = record_outcome(parts[0].strip(), parts[1].strip(),
                                "Recorded through terminal", parts[2].strip() if len(parts) > 2 else "")
        write_audit_event("learning:record", "completed" if result.startswith("Outcome recorded") else "failed")
        return result
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
        result = clear_history()
        write_audit_event("learning:clear", "completed" if result == "Learning history cleared." else "failed")
        return result
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
