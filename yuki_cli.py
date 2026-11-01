"""Thin CLI entry point for Yuki's shared provider and readiness services.

This first CLI slice intentionally offers chat and diagnostics only. It does not
execute tools; task execution remains in the guarded interactive runtime.
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from getpass import getpass
from pathlib import Path

from companion.application import CompanionApplicationService
from companion.conversation import ConversationError, ConversationSession
from companion.memory_consent import CompanionMemory, MemoryConsentError
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore
from core.auth import AuthenticationError, BuiltInAuthService, task_permission_authorizer
from core.config import load_config
from core.portable_paths import data_root
from core.readiness import build_readiness_report
from providers.factory import create_provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yuki", description="Yuki AI assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Report local runtime and Ollama readiness")
    sub.add_parser("provider-status", help="Check the configured provider")

    ask = sub.add_parser("ask", help="Send one prompt to the configured provider")
    ask.add_argument("prompt", help="Prompt text")

    sub.add_parser("chat", help="Start a simple interactive chat (no tools executed)")

    tasks = sub.add_parser("tasks", help="Inspect and supervise durable Yuki tasks")
    task_sub = tasks.add_subparsers(dest="task_command", required=True)
    task_list = task_sub.add_parser("list", help="List authorized task summaries")
    task_list.add_argument("--state", choices=["queued", "running", "waiting_approval", "succeeded", "failed", "cancelled", "interrupted"])
    task_list.add_argument("--limit", type=int, default=50)
    task_inspect = task_sub.add_parser("inspect", help="Inspect an authorized task")
    task_inspect.add_argument("task_id")
    for action in ("approve", "cancel", "retry"):
        task_action = task_sub.add_parser(action, help=f"{action.capitalize()} an authorized task")
        task_action.add_argument("task_id")
        task_action.add_argument("--version", type=int, required=True,
                                 help="Current task version shown by yuki tasks inspect")

    auth = sub.add_parser("auth", help="Manage your local Yuki sign-in session")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_sub.add_parser("bootstrap-admin", help="Create the first local Yuki administrator")
    auth_sub.add_parser("login", help="Sign in and store a protected local session")
    auth_sub.add_parser("status", help="Show the current authenticated identity")
    auth_sub.add_parser("logout", help="Revoke and remove the current local session")
    return parser


def _write_json(value, output_fn: Callable[[str], object] = print) -> None:
    output_fn(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _safe_ask(provider, prompt: str) -> tuple[str, int]:
    """Return a sanitized response and non-zero status for provider failures."""
    if not isinstance(prompt, str) or not prompt.strip():
        return "Yuki: Please enter a message.", 2
    try:
        if not provider.is_available():
            return "Yuki: The configured provider is unavailable.", 3
        answer = provider.generate(prompt.strip())
    except Exception as exc:
        return f"Yuki: Provider request failed ({type(exc).__name__}).", 4
    if not isinstance(answer, str) or not answer.strip():
        return "Yuki: Provider returned an invalid response.", 4
    return answer, 0


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    config_loader: Callable = load_config,
    provider_factory: Callable = create_provider,
    readiness_builder: Callable = build_readiness_report,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], object] = print,
    memory_factory: Callable = CompanionMemory,
    password_input: Callable[[str], str] = getpass,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "auth":
        root = data_root() / ".yuki"
        try:
            root.mkdir(parents=True, exist_ok=True)
            try:
                root.chmod(0o700)
            except OSError:
                pass
            auth_service = BuiltInAuthService(str(root / "auth.sqlite3"))
        except Exception as exc:
            output_fn("Yuki authentication could not initialize (" + type(exc).__name__ + ").")
            return 5
        session_path = root / "session.token"

        if args.auth_command == "bootstrap-admin":
            try:
                username = input_fn("Administrator username: ").strip()
                password = password_input("Administrator password (12+ characters): ")
                confirm = password_input("Confirm administrator password: ")
                if password != confirm:
                    output_fn("Yuki: Passwords do not match.")
                    return 2
                user = auth_service.bootstrap_admin(username, password)
            except (ValueError, PermissionError):
                output_fn("Yuki: Administrator bootstrap was rejected. Check the username/password policy and whether an administrator already exists.")
                return 2
            except Exception as exc:
                output_fn("Yuki authentication failed (" + type(exc).__name__ + ").")
                return 5
            output_fn("Yuki: Initial administrator created for " + user.username + ". Run yuki auth login to sign in.")
            return 0

        if args.auth_command == "login":
            try:
                username = input_fn("Username: ").strip()
                password = password_input("Password: ")
                issued = auth_service.authenticate(username, password)
                temp_path = session_path.with_name("session.token.tmp")
                descriptor = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(issued.token)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, session_path)
                try:
                    session_path.chmod(0o600)
                except OSError:
                    pass
            except AuthenticationError:
                output_fn("Yuki: Invalid username or password.")
                return 2
            except Exception as exc:
                output_fn("Yuki login failed (" + type(exc).__name__ + ").")
                return 5
            output_fn("Yuki: Signed in as " + issued.user.username + " (" + issued.user.role + ").")
            return 0

        if args.auth_command == "status":
            try:
                token = session_path.read_text(encoding="utf-8").strip()
                user = auth_service.resolve_session(token)
            except FileNotFoundError:
                user = None
            except Exception as exc:
                output_fn("Yuki session could not be checked (" + type(exc).__name__ + ").")
                return 5
            if user is None:
                output_fn("Yuki: Not signed in.")
                return 1
            _write_json({"authenticated": True, "user_id": user.user_id, "username": user.username, "role": user.role, "permissions": sorted(user.permissions)}, output_fn)
            return 0

        if args.auth_command == "logout":
            try:
                token = session_path.read_text(encoding="utf-8").strip()
                revoked = auth_service.revoke_session(token)
                session_path.unlink()
            except FileNotFoundError:
                revoked = False
            except Exception as exc:
                output_fn("Yuki logout failed (" + type(exc).__name__ + ").")
                return 5
            output_fn("Yuki: Session revoked." if revoked else "Yuki: No active local session.")
            return 0

    if args.command == "tasks":
        root = data_root() / ".yuki"
        session_path = root / "session.token"
        try:
            token = session_path.read_text(encoding="utf-8").strip()
            auth_service = BuiltInAuthService(str(root / "auth.sqlite3"))
            user = auth_service.resolve_session(token)
            if user is None:
                output_fn("Yuki: Sign in first with yuki auth login.")
                return 1
            database_path = os.environ.get("YUKI_TASK_DB", str(root / "tasks.sqlite3"))
            runtime = TaskRuntime(
                WorkflowRunStore(database_path),
                authorize=task_permission_authorizer(
                    auth_service, session_token_provider=lambda: session_path.read_text(encoding="utf-8").strip(),
                ),
            )
            actor_id = user.user_id
        except FileNotFoundError:
            output_fn("Yuki: Sign in first with yuki auth login.")
            return 1
        except Exception as exc:
            output_fn("Yuki task service could not initialize (" + type(exc).__name__ + ").")
            return 5

        try:
            if args.task_command == "list":
                if not 1 <= args.limit <= 100:
                    output_fn("Yuki: --limit must be between 1 and 100.")
                    return 2
                records = runtime.list_task_summaries(
                    actor_id=actor_id, state=args.state, limit=args.limit,
                )
                _write_json([{
                    "task_id": item.task_id, "name": item.name, "state": item.state,
                    "version": item.version, "attempts": item.attempts,
                    "created_at": item.created_at, "updated_at": item.updated_at,
                } for item in records], output_fn)
                return 0
            if args.task_command == "inspect":
                record = runtime.get(actor_id=actor_id, task_id=args.task_id)
                _write_json({
                    "task_id": record.task_id, "name": record.name, "state": record.state,
                    "steps": record.steps, "result": record.result, "error_code": record.error_code,
                    "version": record.version, "attempts": record.attempts,
                    "created_at": record.created_at, "updated_at": record.updated_at,
                    "lease_owner": record.lease_owner, "lease_expires_at": record.lease_expires_at,
                }, output_fn)
                return 0
            if args.task_command == "approve":
                record = runtime.approve(
                    actor_id=actor_id, task_id=args.task_id, session_id=token,
                    expected_version=args.version,
                )
            elif args.task_command == "cancel":
                record = runtime.cancel(
                    actor_id=actor_id, task_id=args.task_id, expected_version=args.version,
                )
            else:
                record = runtime.retry_failed(
                    actor_id=actor_id, task_id=args.task_id, expected_version=args.version,
                )
            _write_json({"task_id": record.task_id, "state": record.state, "version": record.version}, output_fn)
            return 0
        except PermissionError:
            output_fn("Yuki: Your account is not authorized for this task action.")
            return 3
        except (WorkflowConflict, ValueError) as exc:
            output_fn("Yuki: Task was not changed. Refresh its details and try again (" + type(exc).__name__ + ").")
            return 2
        except KeyError:
            output_fn("Yuki: Task not found.")
            return 4
        except Exception as exc:
            output_fn("Yuki task operation failed (" + type(exc).__name__ + ").")
            return 5

    if args.command == "doctor":
        report = readiness_builder().to_dict()
        _write_json(report, output_fn)
        return 0

    try:
        config = config_loader()
        provider = provider_factory(config)
    except Exception as exc:
        output_fn(f"Yuki could not initialize the configured provider ({type(exc).__name__}).")
        return 2

    if args.command == "provider-status":
        available = False
        try:
            available = bool(provider.is_available())
        except Exception:
            pass
        _write_json({
            "provider": type(provider).__name__,
            "available": available,
            "status": "ready" if available else "unavailable",
        }, output_fn)
        return 0 if available else 3

    if args.command == "ask":
        answer, status = _safe_ask(provider, args.prompt)
        output_fn(answer)
        return status

    if args.command == "chat":
        session = ConversationSession(provider)
        try:
            memory = memory_factory()
        except Exception as exc:
            output_fn(
                "Yuki could not initialize companion memory ("
                + type(exc).__name__
                + "). No plaintext fallback was used."
            )
            return 5
        app = CompanionApplicationService(provider, memory=memory, session=session)
        output_fn("Yuki companion chat. Memory is off by default. Use /memory on to enable explicit saves. Tool execution is disabled.")
        while True:
            try:
                message = input_fn("You> ")
            except (EOFError, KeyboardInterrupt):
                output_fn("")
                return 0
            command = message.strip().lower()
            if command in {"/quit", "/exit"}:
                return 0
            if command == "/role":
                output_fn("Yuki> Available roles: companion, tutor, interviewer, developer, security, marketing. Use /role NAME.")
                continue
            if command.startswith("/role "):
                role_name = command.split(None, 1)[1]
                try:
                    role = app.set_role(role_name)
                except ValueError:
                    output_fn("Yuki> Unknown role. Available: companion, tutor, interviewer, developer, security, marketing.")
                else:
                    output_fn("Yuki> Interaction style changed to " + role["name"] + ". Permissions are unchanged.")
                continue
            if command in {"/memory", "/memory status"}:
                output_fn(
                    "Yuki> Memory saving is " + ("on." if memory.enabled else "off.")
                    + " Saved-memory context is " + ("on for this session." if memory.context_enabled else "off.")
                )
                continue
            if command == "/memory on":
                memory.enable()
                output_fn("Yuki> Memory enabled. I will only save information when you explicitly use /remember.")
                continue
            if command == "/memory off":
                memory.disable()
                output_fn("Yuki> Memory saving and context use disabled. Existing saved entries remain until you explicitly delete them.")
                continue
            if command == "/memory use on":
                memory.enable_context()
                output_fn("Yuki> Saved-memory context enabled for this session. Saved notes are treated as untrusted context, not instructions.")
                continue
            if command == "/memory use off":
                memory.disable_context()
                output_fn("Yuki> Saved-memory context disabled for this session.")
                continue
            if command.startswith("/remember "):
                try:
                    saved = memory.remember(message.strip()[len("/remember "):])
                except (MemoryConsentError, ValueError):
                    output_fn("Yuki> Memory is off or the entry is invalid. Use /memory on first; entries are limited to 4000 characters.")
                else:
                    output_fn("Yuki> Saved memory " + saved["id"] + ".")
                continue
            if command == "/memories":
                try:
                    entries = memory.list_memories()
                except Exception:
                    output_fn("Yuki> I could not read the local memory store.")
                else:
                    if not entries:
                        output_fn("Yuki> No saved memories.")
                    for item in entries:
                        output_fn("Yuki> [" + item["id"] + "] (" + item.get("category", "note") + ") " + item["text"])
                continue
            if command.startswith("/forget "):
                memory_id = message.strip().split(None, 1)[1]
                try:
                    deleted = memory.forget(memory_id)
                except Exception:
                    deleted = False
                output_fn("Yuki> " + ("Memory deleted." if deleted else "No matching memory was deleted."))
                continue
            if command == "/memory export":
                try:
                    _write_json(memory.list_memories(limit=200), output_fn)
                except Exception:
                    output_fn("Yuki> I could not export the local memory store.")
                continue
            if command == "/memory clear confirm":
                try:
                    count = memory.clear()
                    output_fn("Yuki> Deleted " + str(count) + " saved memories.")
                except Exception:
                    output_fn("Yuki> I could not clear the local memory store.")
                continue
            if command.startswith("/memory clear"):
                output_fn("Yuki> To delete all saved memories, type exactly /memory clear confirm.")
                continue
            if command == "/reset":
                app.reset_conversation()
                output_fn("Yuki> This conversation has been cleared.")
                continue
            if command in {"/help", "/?"}:
                output_fn("Yuki> I can chat with context during this session. Commands: /role NAME, /memory on|off, /memory use on|off, /remember TEXT, /memories, /forget ID, /memory export, /memory clear CONFIRM, /reset, /quit.")
                continue
            if not message.strip():
                continue
            try:
                reply = app.respond(message)
            except ValueError:
                output_fn("Yuki> Please send a message of up to 4000 characters.")
                continue
            except ConversationError as exc:
                output_fn("Yuki> " + str(exc))
                continue
            output_fn("Yuki> " + reply.message)


def main() -> int:
    try:
        return run_cli()
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
