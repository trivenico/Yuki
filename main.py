"""Integrated guarded Yuki runtime.

Run with: python main.py
The legacy feature-rich CLI remains available with: python yuki.py
"""
from datetime import datetime
from pathlib import Path

from companion import DEFAULT_PERSONA
from companion.memory import MemoryStore
from companion.proactive import ReminderQueue
from core.agent import YukiCoreAgent
from core.config import load_config
from core.permissions import PermissionGate
from skills.registry import SkillRegistry


def _ask_approval(request):
    print("\nPermission request")
    print(f"  Action: {request.action}")
    print(f"  Reason: {request.reason}")
    print(f"  Risk:   {request.risk}")
    try:
        answer = input("Approve this single action? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "y"


def _confirm(action, reason, risk="medium"):
    return _ask_approval(type("ApprovalPrompt", (), {
        "action": action, "reason": reason, "risk": risk
    })())


def build_agent(config):
    workspace = Path(config["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry.defaults(workspace)
    return YukiCoreAgent(registry=registry, permission_gate=PermissionGate(_ask_approval))


def print_help():
    print("""Yuki guarded core
  /help                         Show commands
  /status                       Show configured capabilities
  /skills                       List registered skills
  /skill NAME [ARG]             Request one skill (approval required)
  /memory add TEXT              Save a local note (approval required)
  /memory list                  List recent notes
  /memory delete ID             Delete a note (approval required)
  /memory clear                 Delete all notes (approval required)
  /reminder add ISO_DATETIME | TEXT
                                Add an ISO-8601 reminder with timezone
  /reminders                    List reminders
  /reminders due                Show and mark due reminders delivered
  /quit                         Exit

Initial skills are read-only: time, system, files. Memory and reminders are local.
Voice, model reasoning, arbitrary shell execution, and security scanning are not enabled.
""")


def main():
    config = load_config()
    agent = build_agent(config)
    memory = MemoryStore(Path(config["workspace"]) / "yuki_memory.json")
    reminders = ReminderQueue(Path(config["workspace"]) / "yuki_reminders.json")
    print(DEFAULT_PERSONA.greeting)
    print("Yuki guarded core is ready. Type /help for commands.")
    while True:
        try:
            command = input("\nYou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nYuki > Goodbye.")
            return
        if not command:
            continue
        if command in {"/quit", "/exit"}:
            print("Yuki > Goodbye.")
            return
        if command == "/help":
            print_help()
        elif command == "/status":
            print(f"Persona: {agent.persona.name}; wake word: {agent.persona.wake_word}")
            print(f"Workspace: {config['workspace']}")
            print("AI provider: not connected; voice: disabled; security lab: disabled")
            print(f"Registered skills: {', '.join(agent.registry.names())}")
        elif command == "/skills":
            for skill in agent.available_skills():
                print(f"- {skill['name']} [{skill['risk']}]: {skill['description']}")
        elif command.startswith("/skill "):
            parts = command.split(maxsplit=2)
            name = parts[1] if len(parts) > 1 else ""
            argument = parts[2] if len(parts) > 2 else ""
            response = agent.run_skill(name, argument)
            print(f"Yuki > {response.message}")
            if response.ok and response.data is not None:
                print(response.data)
        elif command.startswith("/memory add "):
            note = command[len("/memory add "):].strip()
            if _confirm("memory:add", "Save the supplied note to local Yuki memory."):
                try:
                    item = memory.add(note)
                    print(f"Yuki > Saved note {item['id']}.")
                except (ValueError, RuntimeError) as exc:
                    print(f"Yuki > Memory was not changed: {exc}")
            else:
                print("Yuki > Memory write cancelled.")
        elif command == "/memory list":
            for item in memory.list():
                print(f"{item['id']} [{item.get('category', 'note')}] {item['created_at']}: {item['text']}")
        elif command.startswith("/memory delete "):
            memory_id = command[len("/memory delete "):].strip()
            if _confirm("memory:delete", f"Delete memory entry {memory_id}."):
                print("Yuki > Deleted." if memory.delete(memory_id) else "Yuki > Entry not found.")
            else:
                print("Yuki > Memory deletion cancelled.")
        elif command == "/memory clear":
            if _confirm("memory:clear", "Permanently delete all local Yuki memory entries.", "high"):
                print(f"Yuki > Deleted {memory.clear()} memory entries.")
            else:
                print("Yuki > Memory clear cancelled.")
        elif command.startswith("/reminder add "):
            payload = command[len("/reminder add "):]
            if "|" not in payload:
                print("Yuki > Use /reminder add 2026-12-31T18:00:00+00:00 | reminder text")
                continue
            timestamp, text = payload.split("|", 1)
            try:
                due_at = datetime.fromisoformat(timestamp.strip())
                if due_at.tzinfo is None or due_at.utcoffset() is None:
                    raise ValueError("include a timezone offset, such as +00:00")
                item = reminders.add(text.strip(), due_at)
                print(f"Yuki > Reminder saved ({item['id']}). Yuki does not run scheduled actions.")
            except (ValueError, RuntimeError) as exc:
                print(f"Yuki > Reminder was not saved: {exc}")
        elif command == "/reminders":
            for item in reminders.list():
                state = "delivered" if item.get("delivered") else "pending"
                print(f"{item['id']} [{state}] {item['due_at']}: {item['text']}")
        elif command == "/reminders due":
            due = reminders.due()
            if not due:
                print("Yuki > No reminders are due.")
            for item in due:
                print(f"Yuki reminder: {item['text']} (due {item['due_at']})")
        else:
            print("Yuki > This runtime does not have an LLM connected yet. Use /help for available commands.")


if __name__ == "__main__":
    main()
