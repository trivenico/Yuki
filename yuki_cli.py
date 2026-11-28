"""Thin CLI entry point for Yuki's provider, diagnostics and persistent chat."""
from __future__ import annotations

import argparse
import json
import uuid
from getpass import getpass
from pathlib import Path
from collections.abc import Callable, Sequence

from core.config import load_config
from core.local_cli_runtime import build_local_conversation_runtime
from core.portable_paths import desktop_data_root
from core.auth import BuiltInAuthService
from core.approval_audit import SQLiteApprovalAuditLog
from ui.desktop.storage import prepare_private_desktop_root
from core.readiness import ResourceInventory, build_readiness_report
from core.resource_budget import calculate_worker_budget, worker_budget_to_dict
from core.worker_health import read_worker_health
from providers.factory import create_provider
from core.provider_setup import run_provider_setup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yuki", description="Yuki AI assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Report local runtime and Ollama readiness")
    sub.add_parser("setup", help="Choose a provider and save non-secret preferences")
    sub.add_parser("provider-status", help="Check the configured provider")
    sub.add_parser("provider-test", help="Send a minimal live request to verify provider connectivity")

    ask = sub.add_parser("ask", help="Send one prompt to the configured provider")
    ask.add_argument("prompt", help="Prompt text")

    sub.add_parser("chat", help="Start persistent local conversation chat")

    worker = sub.add_parser("worker", help="Inspect the local worker runtime")
    worker_commands = worker.add_subparsers(dest="worker_command", required=True)
    worker_status = worker_commands.add_parser("status", help="Show read-only worker resource and snapshot diagnostics")
    worker_status.add_argument("--health-file", help="Optional path to the private worker health snapshot")

    auth = sub.add_parser("auth", help="Manage the local Yuki owner account")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_commands.add_parser("bootstrap-admin", help="Create the first local administrator account")
    recovery = auth_commands.add_parser("recovery-list", help="Inspect unresolved guarded executions (read-only)")
    recovery.add_argument("--audit-db", required=True, help="Existing approval audit SQLite database; never created by this command")
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


def _run_persistent_chat(runtime, *, input_fn, output_fn) -> int:
    """Run a durable chat session; commands never execute local tools."""
    conversations = runtime.conversations
    owner_id = runtime.owner_id
    items = conversations.list(owner_id=owner_id, limit=1)
    current = items[0] if items else conversations.create(owner_id=owner_id)
    output_fn(
        "Yuki persistent chat. /new, /list, /use ID, /history, /help, /quit. "
        "Tool execution is disabled."
    )

    while True:
        try:
            message = input_fn("You> ")
        except (EOFError, KeyboardInterrupt):
            output_fn("")
            return 0
        if not isinstance(message, str):
            output_fn("Yuki: Input must be text.")
            continue
        command = message.strip()
        lowered = command.lower()
        if lowered in {"/quit", "/exit"}:
            return 0
        if lowered == "/help":
            output_fn("Commands: /new, /list, /use ID, /history, /quit.")
            continue
        if lowered == "/new":
            current = conversations.create(owner_id=owner_id)
            output_fn("Yuki: Started a new conversation.")
            continue
        if lowered == "/list":
            listed = conversations.list(owner_id=owner_id, limit=50)
            if not listed:
                output_fn("Yuki: No saved conversations.")
            for item in listed:
                marker = "*" if item["conversation_id"] == current["conversation_id"] else " "
                output_fn(f"{marker} {item['conversation_id']}  {item.get('title', 'Conversation')}")
            continue
        if lowered == "/history":
            history = conversations.history(
                owner_id=owner_id, conversation_id=current["conversation_id"], limit=20,
            )
            if not history:
                output_fn("Yuki: This conversation is empty.")
            for item in history:
                speaker = "You" if item["role"] == "user" else "Yuki"
                output_fn(f"{speaker}> {item['content']}")
            continue
        if lowered.startswith("/use "):
            requested = command[5:].strip()
            listed = conversations.list(owner_id=owner_id, limit=50)
            selected = next(
                (item for item in listed if item["conversation_id"] == requested), None
            )
            if selected is None:
                output_fn("Yuki: Conversation not found.")
            else:
                current = selected
                output_fn("Yuki: Switched conversation.")
            continue
        if lowered.startswith("/"):
            output_fn("Yuki: Unknown command. Type /help.")
            continue
        if not command:
            continue

        try:
            result = conversations.reply(
                owner_id=owner_id,
                conversation_id=current["conversation_id"],
                user_text=command,
                request_id=str(uuid.uuid4()),
                expected_version=current["version"],
                tools=[],
            )
            current["version"] = result["version"]
            output_fn("Yuki> " + result["answer"])
            if result["status"] not in {"completed", "replayed"}:
                output_fn(f"Yuki: Turn status: {result['status']}.")
        except KeyboardInterrupt:
            # The service releases its generation lease while unwinding. Its
            # durable running record is reconciled on the next turn, without
            # replaying a request that may have reached a provider or tool.
            output_fn("Yuki: Generation interrupted. The incomplete turn will be reconciled before your next message.")
            continue
        except Exception as exc:
            output_fn(f"Yuki: Conversation failed ({type(exc).__name__}).")
            return 4


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    config_loader: Callable = load_config,
    provider_factory: Callable = create_provider,
    readiness_builder: Callable = build_readiness_report,
    resource_inventory_builder: Callable = ResourceInventory.detect,
    conversation_runtime_factory: Callable = build_local_conversation_runtime,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], object] = print,
    password_input: Callable[[str], str] | None = None,
    auth_service_factory: Callable[[str], object] = BuiltInAuthService,
    audit_log_factory: Callable[[str], object] = SQLiteApprovalAuditLog,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "auth" and args.auth_command == "bootstrap-admin":
        try:
            root = prepare_private_desktop_root(desktop_data_root())
            auth_service = auth_service_factory(str(root / "auth.sqlite3"))
            username = input_fn("Choose Yuki owner username: ").strip()
            read_password = password_input or getpass
            password = read_password("Choose a strong Yuki owner password: ")
            confirmation = read_password("Confirm Yuki owner password: ")
            if password != confirmation:
                output_fn("Yuki owner setup cancelled: passwords did not match.")
                return 2
            auth_service.bootstrap_admin(username, password)
            output_fn("Yuki owner account created. Launch Yuki Desktop to sign in.")
            return 0
        except Exception as exc:
            output_fn(f"Yuki owner setup failed ({type(exc).__name__}).")
            return 2

    if args.command == "auth" and args.auth_command == "recovery-list":
        auth_service = None
        issued_session = None
        try:
            root = prepare_private_desktop_root(desktop_data_root())
            auth_path = root / "auth.sqlite3"
            audit_path = Path(args.audit_db).expanduser()
            # Never let a typo silently create an empty audit database and
            # report a misleadingly clean recovery queue.
            if not auth_path.is_file() or auth_path.is_symlink():
                raise FileNotFoundError("owner authentication database is unavailable")
            if not audit_path.is_file() or audit_path.is_symlink():
                raise FileNotFoundError("existing approval audit database is required")
            auth_service = auth_service_factory(str(auth_path))
            username = input_fn("Yuki owner username: ")
            read_password = password_input or getpass
            issued_session = auth_service.authenticate(
                username, read_password("Yuki owner password: ")
            )
            if issued_session.user.role != "admin" or "workflow:approve" not in issued_session.user.permissions:
                raise PermissionError("owner administrator permission is required")
            audit_log = audit_log_factory(str(audit_path))
            unresolved = audit_log.list_unresolved_executions(limit=100)
            _write_json({
                "status": "verified",
                "recovery_required_count": len(unresolved),
                "items": unresolved,
                "read_only": True,
                "execution_started": False,
                "automatic_retry": False,
            }, output_fn)
            return 0
        except Exception as exc:
            output_fn(f"Yuki recovery inspection failed ({type(exc).__name__}).")
            return 2
        finally:
            if auth_service is not None and issued_session is not None:
                try:
                    auth_service.revoke_session(issued_session.token)
                except Exception:
                    pass

    if args.command == "setup":
        return run_provider_setup(input_fn=input_fn, output_fn=output_fn)

    if args.command == "doctor":
        report = readiness_builder().to_dict()
        _write_json(report, output_fn)
        return 0

    if args.command == "worker" and args.worker_command == "status":
        # This command intentionally does not construct a provider, open the
        # task database, or start a worker. The current guard state belongs to
        # the worker process and is not persisted yet.
        resources = resource_inventory_builder()
        effective_cpu_count = getattr(
            resources, "effective_cpu_count", resources.logical_cpu_count,
        )
        budget = calculate_worker_budget(
            logical_cpu_count=effective_cpu_count,
            memory_effective_available_bytes=resources.memory_effective_available_bytes,
            requested_concurrency=1,
        )
        _write_json({
            "worker_runtime": "not_started_by_status_command",
            "resource_profile": "single_foreground_worker",
            "worker_budget": worker_budget_to_dict(budget),
            "cpu": {
                "logical_count": resources.logical_cpu_count,
                "effective_count": effective_cpu_count,
                "cgroup_quota": getattr(resources, "cpu_cgroup_limit", None),
                "cgroup_source": getattr(resources, "cpu_cgroup_source", "unavailable"),
            },
            "memory": {
                "total_bytes": resources.memory_total_bytes,
                "available_bytes": resources.memory_available_bytes,
                "source": resources.memory_source,
                "cgroup_limit_bytes": resources.memory_cgroup_limit_bytes,
                "cgroup_available_bytes": resources.memory_cgroup_available_bytes,
                "effective_available_bytes": resources.memory_effective_available_bytes,
            },
            "memory_guard": {
                "state": "not_active_worker_not_started",
                "thresholds": "not_configured_by_status_command",
            },
            "logical_cpu_count": resources.logical_cpu_count,
            "effective_cpu_count": effective_cpu_count,
            "cpu_cgroup_limit": getattr(resources, "cpu_cgroup_limit", None),
            "cpu_cgroup_source": getattr(resources, "cpu_cgroup_source", "unavailable"),
            "default_max_workers": 1,
            "memory_total_bytes": resources.memory_total_bytes,
            "memory_available_bytes": resources.memory_available_bytes,
            "memory_source": resources.memory_source,
            "memory_cgroup_limit_bytes": resources.memory_cgroup_limit_bytes,
            "memory_cgroup_available_bytes": resources.memory_cgroup_available_bytes,
            "memory_effective_available_bytes": resources.memory_effective_available_bytes,
            "memory_guard_state": "process_local_not_persisted",
            "health_snapshot": (
                read_worker_health(args.health_file).__dict__
                if args.health_file else {"state": "not_configured"}
            ),
            "task_execution_started": False,
        }, output_fn)
        return 0

    if args.command == "chat":
        runtime = None
        try:
            config = config_loader()
            runtime = conversation_runtime_factory(config)
            return _run_persistent_chat(runtime, input_fn=input_fn, output_fn=output_fn)
        except Exception as exc:
            output_fn(f"Yuki could not initialize persistent chat ({type(exc).__name__}).")
            return 2
        finally:
            if runtime is not None:
                try:
                    runtime.close()
                except Exception:
                    output_fn("Yuki: Chat storage could not close cleanly.")

    try:
        config = config_loader()
        provider = provider_factory(config)
    except Exception as exc:
        output_fn(f"Yuki could not initialize the configured provider ({type(exc).__name__}).")
        return 2

    if args.command == "provider-test":
        try:
            if not provider.is_available():
                _write_json({
                    "provider": type(provider).__name__,
                    "status": "unavailable",
                    "verified": False,
                }, output_fn)
                return 3
            # This fixed, non-sensitive probe is sent only after the user
            # explicitly invokes provider-test. The model response is not
            # printed or persisted.
            answer = provider.generate("Reply with exactly: YUKI_CONNECTION_OK")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("empty response")
        except Exception as exc:
            _write_json({
                "provider": type(provider).__name__,
                "status": "request_failed",
                "error_type": type(exc).__name__,
                "verified": False,
            }, output_fn)
            return 4
        _write_json({
            "provider": type(provider).__name__,
            "status": "connection_verified",
            "verified": True,
        }, output_fn)
        return 0

    if args.command == "provider-status":
        available = False
        try:
            available = bool(provider.is_available())
        except Exception:
            pass
        availability_scope = getattr(provider, "availability_scope", "provider_defined")
        if not available:
            status = "unavailable"
        elif availability_scope == "executable_installed_only":
            status = "installed_authentication_unverified"
        elif availability_scope == "api_key_present_only":
            status = "configured_connection_unverified"
        else:
            status = "ready"
        _write_json({
            "provider": type(provider).__name__,
            "available": available,
            "availability_scope": availability_scope,
            "status": status,
        }, output_fn)
        # Exit zero only when the provider's own check establishes readiness.
        return 0 if status == "ready" else 3

    if args.command == "ask":
        answer, status = _safe_ask(provider, args.prompt)
        output_fn(answer)
        return status

    return 2


def main() -> int:
    try:
        return run_cli()
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
