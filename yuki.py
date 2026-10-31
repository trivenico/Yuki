"""Yuki: beginner-friendly terminal agent prototype with local safety controls."""

import json
from datetime import datetime, timezone
from pathlib import Path

from yuki_agent import YukiAgent
from yuki_learning import record_outcome, suggest_lessons, load_history, clear_history
from yuki_providers import ProviderManager
from yuki_security import SecurityPolicy, load_audit_events, sanitize_text, write_audit_event
from yuki_tasks import load_tasks, save_tasks, add_task, list_tasks, complete_task, delete_task
from yuki_tools import TOOLS, run_tool
from skills.registry import SkillRegistry
from core.guarded_skill_runtime import OneShotSkillApprovals
from core.approved_skill_runtime import ApprovedSkillRuntime
from core.guarded_tool_runtime import GuardedToolRuntime
from core.durable_task_engine import DurableTaskEngine
from core.durable_skill_worker import DurableSkillWorker
from core.sync_runtime import SyncRuntime
from core.sync_client import SyncClientError
from core.task_sync_adapter import TaskSyncAdapter
from core.application_runtime import YukiApplicationRuntime
from core.skill_pack_sync import SkillPackSyncAdapter

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
_DURABLE_TASK_DB = Path(__file__).with_name('.yuki') / 'durable_tasks.sqlite3'


def _get_durable_task_engine():
    _DURABLE_TASK_DB.parent.mkdir(parents=True, exist_ok=True)
    return DurableTaskEngine(_DURABLE_TASK_DB)


def _build_approved_skill_runtime():
    registry = SkillRegistry.defaults(Path.cwd())
    runtime = ApprovedSkillRuntime(
        registry,
        _get_skill_approval_store(),
        invocation_approvals=_INVOCATION_APPROVALS,
        audit=lambda action, outcome: write_audit_event(action, outcome),
    )
    return registry, runtime


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
  /sync status                    Show sync hub, device and outbox status
  /sync pair                      Pair this device using a one-time code
  /sync tasks preview             Preview eligible task fields
  /sync tasks queue               Queue approved task fields locally
  /sync run                       Synchronize supported task upserts
  /tool NAME [argument]           Run an explicitly allowed tool
  /skill list                     List registered skills
  /skill diagnostics              Show installed skill-pack load health
  /skill inspect NAME             Inspect a registered skill's metadata
  /skill approve NAME [argument]  Approve one exact high-risk invocation
  /skill invoke NAME [argument]   Invoke a registered skill through the guard
  /durable goal create             Create a durable task goal
  /durable task submit KEY | GOAL_ID | SKILL | ARGUMENT
  /durable task list GOAL_ID       List durable tasks for a goal
  /durable task cancel TASK_ID     Cancel a durable task
  /durable run                     Run one approved registered-skill task
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

The runtime defaults to the real OpenAI API. The mock adapter is reserved for automated tests and is not available in the runtime provider registry.
""")


def handle_command(message, memory, tasks, agent, providers, policy=None, conversation_context=None):
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
    if command == "/sync" or command.startswith("/sync "):
        parts = command.split(maxsplit=2)
        action = parts[1] if len(parts) > 1 else "status"
        try:
            runtime = SyncRuntime()
            try:
                if action == "status":
                    return runtime.status()
                if action == "pair":
                    if len(parts) != 2:
                        return "Use /sync pair; Yuki will request the one-time code without echoing it."
                    if not runtime.endpoint:
                        return "Set YUKI_SYNC_URL to your HTTPS Yuki Sync Hub endpoint first."
                    if runtime.vault is None:
                        return "OS credential storage is unavailable. Install requirements-sync.txt and verify the Windows Credential Manager backend."
                    from getpass import getpass
                    code = getpass("One-time Yuki pairing code (input hidden): ")
                    if not code.strip():
                        return "Pairing cancelled."
                    result = runtime.pair(code)
                    write_audit_event("sync:pair", "completed")
                    return result
                if action == "tasks":
                    subcommand = parts[2] if len(parts) > 2 else "preview"
                    if subcommand == "preview":
                        preview = TaskSyncAdapter.preview(tasks)
                        return (
                            f"Task sync preview: {preview['total']} eligible tasks; "
                            f"{preview['tracked']} have sync tracking metadata. "
                            "Only description, completion state and creation timestamp are shared. "
                            "Nothing has been queued or uploaded. Use /sync tasks queue to opt in."
                        )
                    if subcommand == "queue":
                        if runtime.vault is None or not runtime.vault.load():
                            return "Pair this device first with /sync pair. No tasks were queued."
                        device_id = runtime.vault.load()[0]
                        queued = TaskSyncAdapter.queue(
                            tasks, device_id, runtime.enqueue_event
                        )
                        if not save_tasks(tasks):
                            write_audit_event("sync:tasks:queue", "metadata_save_failed")
                            return (
                                f"{queued} task events are safely in the local outbox, but "
                                "sync metadata could not be saved. Retry /sync tasks queue; "
                                "event IDs are deterministic so duplicates will not be uploaded."
                            )
                        write_audit_event("sync:tasks:queue", "completed")
                        return (
                            f"{queued} task event(s) added or already present in the local outbox. "
                            "Nothing was uploaded. Review the preview before any future sync run."
                        )
                    return "Usage: /sync tasks preview | queue"
                if action == "skills":
                    adapter = SkillPackSyncAdapter()
                    subcommand = parts[2] if len(parts) > 2 else "preview"
                    if subcommand == "preview":
                        counts = adapter.preview()
                        return (
                            f"Skill sync preview: {counts['installed_data_only_packs']} data-only skill pack(s). "
                            "Executable plugins, approval grants and credentials are never shared."
                        )
                    if subcommand == "queue":
                        saved = runtime.vault.load() if runtime.vault is not None else None
                        if not saved:
                            return "Pair this device first. No skill packs were queued."
                        result = adapter.queue_installed(saved[0], runtime.enqueue_event)
                        return (
                            f"Queued {result['queued']} skill pack event(s); "
                            f"{result['already_queued']} already existed. Nothing was uploaded."
                        )
                    return "Usage: /sync skills preview | queue"
                if action == "chat":
                    store = conversation_context["store"] if conversation_context else None
                    if store is None:
                        return "Local chat history is unavailable."
                    mode = parts[2] if len(parts) > 2 else "preview"
                    if mode == "preview":
                        counts = store.preview_sync()
                        return f"Preview: {counts['conversations']} chats and {counts['messages']} messages."
                    if mode == "queue":
                        saved = runtime.vault.load() if runtime.vault is not None else None
                        if not saved:
                            return "Pair this device first. Nothing was queued."
                        counts = store.queue_sync(saved[0], runtime.enqueue_event)
                        return f"Queued {counts['conversations']} chats and {counts['messages']} messages locally. Nothing uploaded."
                    return "Usage: /sync chat preview | queue"
                if action == "run":
                    if runtime.vault is None or not runtime.vault.load():
                        return "Pair this device first with /sync pair. No network request was made."
                    try:
                        def apply_remote(event):
                            if event["collection"].startswith("conversation_"):
                                if conversation_context is None:
                                    raise SyncClientError("Local chat store is unavailable.")
                                return conversation_context["store"].apply_remote(event)
                            if event["collection"] == "skill_metadata":
                                return SkillPackSyncAdapter().apply_remote(event)
                            return TaskSyncAdapter.apply_remote(event, tasks, save_tasks)
                        result = runtime.sync_once(apply_remote)
                    except SyncClientError as error:
                        write_audit_event("sync:run", "rejected")
                        return f"Sync stopped safely: {error} No unhandled event was acknowledged."
                    write_audit_event("sync:run", "completed")
                    return (
                        "Yuki sync cycle completed. "
                        f"Uploaded: {result['pushed']}; downloaded/applied: {result['pulled']}; "
                        f"conflicts: {result['conflicts']}. "
                        "Task upserts, chat history and data-only skill packs are supported; executable plugins, preferences and deletes are not."
                    )
                return "Usage: /sync status | pair | tasks preview | tasks queue | chat preview | chat queue | skills preview | skills queue | run"
            finally:
                runtime.close()
        except (SyncClientError, ValueError, OSError):
            write_audit_event("sync:" + action, "rejected")
            return "Sync operation unavailable. Check YUKI_SYNC_URL, optional credential storage and hub connectivity."
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
            registry, runtime = _build_approved_skill_runtime()
        except Exception as error:
            return f"Skill runtime unavailable ({type(error).__name__})."
        if len(parts) == 1 or parts[1] == "list":
            return "\n".join(
                f'{item["name"]} [{item["risk"]}]: {item["description"]}'
                for item in registry.describe()
            ) or "No skills registered."
        if parts[1] == "diagnostics":
            errors = getattr(registry, "skill_pack_load_errors", [])
            if not isinstance(errors, list) or not errors:
                return "Skill-pack loading: healthy; no installation or registration errors."
            lines = [f"Skill-pack loading: {len(errors)} error(s)."]
            for item in errors[:10]:
                if not isinstance(item, dict):
                    continue
                filename = sanitize_text(str(item.get("file", "unknown")), 120).replace("\n", " ")
                reason = sanitize_text(str(item.get("error", "unspecified error")), 240).replace("\n", " ")
                lines.append(f"- {filename}: {reason}")
            if len(errors) > 10:
                lines.append(f"- {len(errors) - 10} additional error(s) omitted.")
            return "\n".join(lines)
        if parts[1] == "install":
            source_path = command[len("/skill install"):].strip()
            if not source_path:
                return "Usage: /skill install PATH"
            try:
                from core.skill_packs import install_skill_pack
                target = install_skill_pack(source_path)
                write_audit_event("skill:install", "completed")
                return (
                    f"Installed data-only skill pack: {target.name}. "
                    "It will appear in the skill registry on the next command. "
                    "Imported instructions remain untrusted and do not grant tools or permissions."
                )
            except FileExistsError:
                write_audit_event("skill:install", "rejected")
                return "A skill with that name is already installed."
            except (OSError, ValueError):
                write_audit_event("skill:install", "rejected")
                return "Skill import rejected. Check the SKILL.md frontmatter, size limits, and skill name."

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
            argument = parts[3] if len(parts) == 4 else ""
            try:
                runtime.approve_invocation(parts[2], argument)
                write_audit_event("skill:approval", "granted")
                return f"One-shot invocation approval granted for {parts[2]} and the exact supplied argument."
            except ValueError:
                write_audit_event("skill:approval", "rejected")
                return "Cannot approve an unknown skill."
        if parts[1] == "invoke" and len(parts) >= 3:
            argument = parts[3] if len(parts) == 4 else ""
            result = runtime.invoke(parts[2], argument)
            return result.message if result.ok else f"{result.status}: {result.message}"
        return "Usage: /skill list | diagnostics | install PATH | inspect NAME | approve NAME [argument] | invoke NAME [argument]"

    if command == "/durable" or command.startswith("/durable "):
        durable_command = command[len("/durable"):].strip()
        if durable_command == "goal create":
            try:
                with _get_durable_task_engine() as engine:
                    goal_id = engine.create_goal()
                write_audit_event("durable:goal:create", "completed")
                return f"Durable goal created: {goal_id}"
            except Exception as error:
                return f"Could not create durable goal ({type(error).__name__})."

        if durable_command.startswith("task submit "):
            encoded = durable_command[len("task submit "):]
            fields = [part.strip() for part in encoded.split("|", 3)]
            if len(fields) != 4:
                return "Usage: /durable task submit KEY | GOAL_ID | SKILL | ARGUMENT"
            key, goal_id, skill_name, argument = fields
            if not key or not goal_id or not skill_name or len(argument) > 20000:
                return "Task key, goal ID, skill name, and a bounded argument are required."
            try:
                registry, _runtime = _build_approved_skill_runtime()
                if registry.get(skill_name) is None:
                    return "Cannot queue an unregistered skill."
                with _get_durable_task_engine() as engine:
                    task = engine.submit(
                        goal_id,
                        "skill.invoke",
                        {"skill_name": skill_name, "argument": argument},
                        idempotency_key=key,
                        max_attempts=1,
                    )
                write_audit_event("durable:task:submit", "completed")
                return f"Durable task {task['id']} is {task['state']}. Approve the exact skill argument before running high-risk work."
            except Exception as error:
                write_audit_event("durable:task:submit", "rejected")
                return f"Could not submit durable task ({type(error).__name__})."

        if durable_command.startswith("task list "):
            goal_id = durable_command[len("task list "):].strip()
            if not goal_id:
                return "Usage: /durable task list GOAL_ID"
            try:
                with _get_durable_task_engine() as engine:
                    tasks = engine.list_tasks(goal_id)
                if not tasks:
                    return "No durable tasks found."
                return "\n".join(
                    f"{task['id']} [{task['state']}] {task['task_type']} attempts={task['attempts']}/{task['max_attempts']}"
                    for task in tasks
                )
            except Exception as error:
                return f"Could not list durable tasks ({type(error).__name__})."

        if durable_command.startswith("task cancel "):
            task_id = durable_command[len("task cancel "):].strip()
            if not task_id:
                return "Usage: /durable task cancel TASK_ID"
            try:
                with _get_durable_task_engine() as engine:
                    task = engine.cancel_task(task_id)
                write_audit_event("durable:task:cancel", "completed")
                return f"Durable task {task_id}: {task['state']}."
            except Exception as error:
                return f"Could not cancel durable task ({type(error).__name__})."

        if durable_command == "run":
            try:
                registry, runtime = _build_approved_skill_runtime()
                with _get_durable_task_engine() as engine:
                    engine.recover_expired_leases()
                    result = DurableSkillWorker(engine, runtime, worker_id="yuki-cli-worker").run_once()
                write_audit_event("durable:worker:run", result.get("status", "unknown"))
                return (
                    f"Durable worker: {result.get('state', 'unknown')} "
                    f"({result.get('status', 'unknown')}). "
                    f"{result.get('message', '')}"
                ).strip()
            except Exception as error:
                write_audit_event("durable:worker:run", "failed")
                return f"Durable worker unavailable ({type(error).__name__})."

        return "Usage: /durable goal create | task submit KEY | GOAL_ID | SKILL | ARGUMENT | task list GOAL_ID | task cancel TASK_ID | run"

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
    if command == "/conversation" or command.startswith("/conversation "):
        if conversation_context is None or conversation_context.get("store") is None:
            return "Conversation storage is unavailable."
        parts = command.split(maxsplit=2)
        action = parts[1] if len(parts) > 1 else "list"
        store = conversation_context["store"]
        if action == "list":
            items = store.list_conversations()
            return "\n".join(
                f"{item['conversation_id']} — {item['title']} ({item['message_count']} messages)"
                for item in items[:20]
            ) or "No saved conversations."
        if action == "new":
            now = datetime.now(timezone.utc).isoformat()
            conversation_id = store.create("New conversation", now)
            conversation_context["active_id"] = conversation_id
            return f"Started conversation {conversation_id}."
        if action == "show":
            if len(parts) < 3:
                return "Usage: /conversation show ID"
            messages = store.messages(parts[2])
            return "\n".join(f"{item['role']}: {item['content']}" for item in messages) or "No messages found."
        return "Usage: /conversation list | new | show ID"

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
    application = YukiApplicationRuntime.create()
    providers = application.providers
    agent = application.agent
    policy = SecurityPolicy(allowed_tools=TOOLS.keys())
    memory = load_memory()
    tasks = load_tasks()
    conversation_context = {
        "store": application.conversations,
        "active_id": application.active_conversation_id,
    }
    print("Yuki terminal. Type /help for commands; /quit to exit.")
    print("Safety note: tools are allow-listed; audit records contain metadata, not command arguments.")
    try:
        while True:
            try:
                message = input("\\nYou > ")
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break
            try:
                if message.strip() and not message.strip().startswith("/"):
                    application.begin_turn(message)
                response = handle_command(
                    message, memory, tasks, agent, providers, policy, conversation_context
                )
                if message.strip() and not message.strip().startswith("/") and response and response != "__QUIT__":
                    application.finish_turn(response)
            except Exception as error:
                response = f"Yuki encountered a recoverable error: {type(error).__name__}."
            if response == "__QUIT__":
                print("Yuki > Goodbye.")
                break
            if response:
                print(f"Yuki > {response}")
    finally:
        application.close()


if __name__ == "__main__":
    main()
