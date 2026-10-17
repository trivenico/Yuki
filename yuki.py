"""Yuki: beginner-friendly terminal AI-agent prototype."""

import json
from pathlib import Path

from yuki_agent import YukiAgent
from yuki_learning import record_outcome, suggest_lessons, load_history, clear_history
from yuki_providers import ProviderManager
from yuki_tasks import load_tasks, add_task, list_tasks, complete_task, delete_task
from yuki_tools import TOOLS, run_tool

MEMORY_FILE = Path(__file__).with_name("yuki_memory.json")


def load_memory():
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_memory(memory):
    temp = MEMORY_FILE.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(memory[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(MEMORY_FILE)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def show_help():
    print("""
Yuki commands:
  /help                         Show this help
  /status                       Show provider status
  /providers                    List available provider adapters
  /tool NAME [argument]         Run an allow-listed tool
  /task add DESCRIPTION         Add a task
  /task list                    List tasks
  /task done ID                 Complete a task
  /task delete ID               Delete a task
  /memory                       Show saved notes
  /memory add TEXT              Save a note to memory
  /memory clear                 Clear saved notes
  /learn TASK | OUTCOME | LESSON  Record an outcome
  /lessons TASK                 Show relevant saved lessons
  /learning                     Show recent learning history
  /learning clear               Clear learning history
  /quit                         Exit Yuki

Default provider is a mock provider. It does not connect to an AI service.
""")


def handle_command(message, memory, tasks, agent, providers):
    command = message.strip()
    if not command:
        return ""

    if command in ("/quit", "/exit"):
        return "__QUIT__"
    if command == "/help":
        show_help()
        return ""
    if command == "/status":
        current = providers.current()
        return f"Agent status: {agent.status()} Selected provider: {current.name if current else 'none'}."
    if command == "/providers":
        rows = providers.list_providers()
        return "\n".join(
            f'{p["name"]}: {"implemented" if p["implemented"] else "not implemented"}'
            f'{" (selected)" if p["selected"] else ""}; auth: {", ".join(p["auth_methods"])}'
            for p in rows
        )
    if command.startswith("/tool "):
        parts = command.split(maxsplit=2)
        if len(parts) < 2:
            return "Usage: /tool NAME [argument]"
        return run_tool(parts[1], parts[2] if len(parts) > 2 else "")
    if command.startswith("/task"):
        parts = command.split(maxsplit=2)
        if len(parts) < 2 or parts[1] == "list":
            return list_tasks(tasks)
        if parts[1] == "add":
            return add_task(tasks, parts[2] if len(parts) > 2 else "")
        if parts[1] in ("done", "delete"):
            if len(parts) < 3:
                return f"Usage: /task {parts[1]} ID"
            return complete_task(tasks, parts[2]) if parts[1] == "done" else delete_task(tasks, parts[2])
        return "Usage: /task add DESCRIPTION | list | done ID | delete ID"
    if command == "/memory":
        return "\n".join(f"{i + 1}. {note}" for i, note in enumerate(memory)) or "No saved memory yet."
    if command.startswith("/memory add "):
        note = command[len("/memory add "):].strip()
        if not note:
            return "Please provide a note."
        memory.append(note[:1000])
        if save_memory(memory):
            return "Memory saved locally."
        memory.pop()
        return "Could not save memory."
    if command == "/memory clear":
        old = memory[:]
        memory.clear()
        if save_memory(memory):
            return "Saved memory cleared."
        memory.extend(old)
        return "Could not clear memory."
    if command.startswith("/learn "):
        parts = command[len("/learn "):].split("|", 2)
        if len(parts) < 2:
            return "Usage: /learn TASK | OUTCOME | LESSON"
        return record_outcome(parts[0].strip(), parts[1].strip(),
                              "Recorded through terminal", parts[2].strip() if len(parts) > 2 else "")
    if command.startswith("/lessons"):
        task = command[len("/lessons"):].strip()
        lessons = suggest_lessons(task)
        return "\n".join(f"- {lesson}" for lesson in lessons) if lessons else "No matching lessons saved yet."
    if command == "/learning":
        history = load_history()
        if not history:
            return "No learning history yet."
        return "\n".join(f'{x.get("created_at", "")[:10]} — {x.get("task", "")}: {x.get("outcome", "")}'
                         for x in history[-10:])
    if command == "/learning clear":
        return clear_history()

    related = suggest_lessons(command)
    response = agent.ask(command)
    if related:
        response += "\n\nRelevant saved lessons:\n" + "\n".join(f"- {x}" for x in related)
    return response


def main():
    agent = YukiAgent()
    providers = ProviderManager()
    memory = load_memory()
    tasks = load_tasks()
    print("Yuki terminal prototype. Type /help for commands; /quit to exit.")
    print("Safety note: tools are allow-listed and the default mock provider makes no network calls.")
    while True:
        try:
            message = input("\nYou > ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        try:
            response = handle_command(message, memory, tasks, agent, providers)
        except Exception as error:
            response = f"Yuki encountered a recoverable error: {type(error).__name__}."
        if response == "__QUIT__":
            print("Yuki > Goodbye.")
            break
        if response:
            print(f"Yuki > {response}")


if __name__ == "__main__":
    main()
