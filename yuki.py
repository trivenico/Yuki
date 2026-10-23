"""Yuki: beginner-friendly terminal agent prototype with local safety controls."""

import json
from pathlib import Path

from yuki_agent import YukiAgent
from yuki_learning import record_outcome, suggest_lessons, load_history, clear_history
from yuki_providers import ProviderManager
from yuki_security import SecurityPolicy, load_audit_events, sanitize_text, write_audit_event
from yuki_tasks import load_tasks, add_task, list_tasks, complete_task, delete_task
from yuki_tools import TOOLS, run_tool
from core.plugin_loader import _static_candidates
from skills.registry import SkillRegistry
from core.repo_wiring_audit import audit_python_wiring
from core.skill_registry_audit import audit_skill_registry

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


def show_help():
    print("""
Yuki commands:
  /help                           Show this help
  /status                         Show provider status
  /providers                      List provider adapters and implementation status
  /skills scan                    Statically discover candidate Python skills (does not import them)
  /skills list                    List skills registered in the runtime registry
  /skills inspect NAME            Show metadata for one registered skill
  /skills status                  Compare discovered candidates with registered built-in skills
  /skills audit                   Compare skill modules with explicit registry imports
  /wiring audit                   Inventory Python files and static inbound imports
  /provider select NAME           Select an implemented provider adapter
  /dashboard                      Show Yuki capability and local-state summary
  /tool NAME [argument]           Run an explicitly allowed tool
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
    if command == "/wiring audit":
        try:
            report = audit_python_wiring(Path(__file__).resolve().parent)
        except (OSError, ValueError) as error:
            write_audit_event("wiring:audit", "failed")
            return f"Repository wiring audit failed ({type(error).__name__})."
        counts = report["counts"]
        lines = [f"Static repository wiring audit: {report['python_file_count']} Python files examined."]
        lines.extend(f"  {status}: {count}" for status, count in sorted(counts.items()))
        lines.append(f"Parse/size issues: {len(report['parse_or_size_issues'])}")
        flags = [item for item in report["files"] if item["status"] in {"no_static_inbound_import", "needs_inspection"}]
        lines.append(f"Files needing review (not proof of being unused): {len(flags)}")
        lines.extend(f"  {item['status']}: {item['path']}" for item in flags[:80])
        if len(flags) > 80:
            lines.append(f"  ... and {len(flags) - 80} more; use the JSON audit API for full details.")
        lines.append("Heuristic only: dynamic imports, entry points, plugins, external launchers, and non-Python wiring may not be visible.")
        write_audit_event("wiring:audit", "completed" if not report["parse_or_size_issues"] else "completed_with_issues")
        return "\\n".join(lines)
    if command == "/skills audit":
        try:
            report = audit_skill_registry(Path(__file__).resolve().parent)
        except (OSError, ValueError) as error:
            write_audit_event("skills:audit", "failed")
            return f"Skill registry audit failed ({type(error).__name__})."
        lines = [f"Skill registry wiring audit: {report['skill_module_count']} candidate modules; {report['registry_import_count']} directly imported by registry."]
        lines.append("Directly wired modules: " + (", ".join(report["directly_wired"]) if report["directly_wired"] else "none"))
        lines.append(f"Not directly wired: {len(report['not_directly_wired'])}")
        lines.extend(f"  review: {name}" for name in report["not_directly_wired"])
        lines.extend(f"  source issue: {item['path']} — {item['reason']}" for item in report["issues"])
        lines.append(report["interpretation"])
        write_audit_event("skills:audit", "completed" if not report["issues"] else "completed_with_issues")
        return "\\n".join(lines)
    if command == "/skills status":
        skills_dir = Path(__file__).with_name("skills")
        candidates, failures = _static_candidates([skills_dir])
        try:
            registry = SkillRegistry.defaults(Path(__file__).resolve().parent)
            registered = set(registry.names())
        except Exception as error:
            write_audit_event("skills:status", "failed")
            return f"Could not inspect the built-in skill registry ({type(error).__name__}). No plugin modules were loaded."
        lines = [f"Skill lifecycle status: {len(candidates)} discovered candidate module(s); {len(registered)} built-in registered skill(s)."]
        lines.append("Registered built-ins: " + (", ".join(sorted(registered)) if registered else "none"))
        lines.extend(f"  candidate module: {name}" for name in sorted(candidates))
        lines.extend(f"  source issue: {item['module']} — {item['error']}" for item in failures)
        lines.append("Note: module names and registry skill names are different identifiers; candidates are not assumed registered by name.")
        lines.append("Dynamic plugin registration is not enabled by this status command; trust policy remains explicit.")
        write_audit_event("skills:status", "completed" if not failures else "completed_with_issues")
        return "\\n".join(lines)
    if command == "/skills list":
        try:
            registry = SkillRegistry.defaults(Path(__file__).resolve().parent)
            entries = registry.describe()
        except Exception as error:
            write_audit_event("skills:list", "failed")
            return f"Could not initialize the built-in skill registry ({type(error).__name__})."
        write_audit_event("skills:list", "completed")
        if not entries:
            return "No skills are registered."
        return "\\n".join(f"{item['name']} [{item['risk']}] — {item['description']}" for item in entries)
    if command.startswith("/skills inspect "):
        name = command[len("/skills inspect "):].strip()
        if not name or any(char.isspace() for char in name):
            return "Usage: /skills inspect NAME"
        try:
            registry = SkillRegistry.defaults(Path(__file__).resolve().parent)
            skill = registry.get(name)
        except Exception as error:
            write_audit_event("skills:inspect", "failed")
            return f"Could not initialize the built-in skill registry ({type(error).__name__})."
        if skill is None:
            write_audit_event("skills:inspect", "not_found")
            return f"No registered skill named '{name}'."
        result = f"Name: {skill.name}\\nDescription: {skill.description}\\nRisk: {skill.risk}\\nRequires approval: {bool(getattr(skill, 'requires_approval', True))}\\nExecutable through this command: no (inspection only)."
        write_audit_event("skills:inspect", "completed")
        return result
    if command == "/skills scan":
        skills_dir = Path(__file__).with_name("skills")
        candidates, failures = _static_candidates([skills_dir])
        lines = [f"Static skill scan: {len(candidates)} candidate module(s)."]
        lines.extend(f"  candidate: {name} (not imported; trust and registration not implied)" for name in sorted(candidates))
        lines.extend(f"  scan issue: {item['module']} — {item['error']}" for item in failures)
        if not failures:
            lines.append("No source parsing or size issues detected.")
        lines.append("This command only scans source. It does not trust, import, register, or execute skills.")
        write_audit_event("skills:scan", "completed" if not failures else "completed_with_issues")
        return "\n".join(lines)
    if command.startswith("/skills"):
        return "Usage: /skills scan"
    if command == "/providers":
        return "\n".join(
            f'{item["name"]}: {"implemented" if item["implemented"] else "not implemented"}'
            f'{" (selected)" if item["selected"] else ""}; auth metadata: {", ".join(item["auth_methods"])}'
            for item in providers.list_providers()
        )
    if command == "/tool" or command.startswith("/tool "):
        parts = command.split(maxsplit=2)
        if len(parts) < 2:
            return "Usage: /tool NAME [argument]"
        name = parts[1]
        if not policy.authorize_tool(name):
            write_audit_event("tool:" + name, "denied")
            return "Tool blocked by Yuki's local security policy."
        result = run_tool(name, parts[2] if len(parts) > 2 else "")
        write_audit_event("tool:" + name, "completed")
        return result
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
