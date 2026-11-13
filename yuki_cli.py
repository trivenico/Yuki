"""Thin CLI entry point for Yuki's provider, diagnostics and persistent chat."""
from __future__ import annotations

import argparse
import getpass
import json
import uuid
from collections.abc import Callable, Sequence

from core.config import load_config
from core.local_cli_runtime import build_local_conversation_runtime
from core.readiness import build_readiness_report
from providers.factory import create_provider
from providers.profile_store import OSKeyringVault, ProviderProfileStore
from core.portable_paths import data_root
from core.local_owner_identity import load_or_create_local_owner_id
from yuki_providers import ProviderManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yuki", description="Yuki AI assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Report local runtime and Ollama readiness")
    sub.add_parser("provider-status", help="Check the configured provider")

    profiles = sub.add_parser("provider-profile", help="Manage persistent custom model providers")
    profile_actions = profiles.add_subparsers(dest="profile_action", required=True)
    profile_actions.add_parser("list", help="List owner-scoped profiles without secrets")
    add_profile = profile_actions.add_parser("add", help="Save a custom OpenAI-compatible endpoint")
    add_profile.add_argument("--name", required=True)
    add_profile.add_argument("--endpoint", required=True)
    add_profile.add_argument("--model", required=True)
    add_profile.add_argument("--approved-host", required=True)
    select_profile = profile_actions.add_parser("select", help="Select a saved profile for CLI requests")
    select_profile.add_argument("name")
    remove_profile = profile_actions.add_parser("remove", help="Remove a saved profile and vault credential")
    remove_profile.add_argument("name")
    verify_profile = profile_actions.add_parser("verify", help="Check local profile and vault readiness (no network request)")
    verify_profile.add_argument("name")

    ask = sub.add_parser("ask", help="Send one prompt to the configured provider")
    ask.add_argument("prompt", help="Prompt text")

    sub.add_parser("chat", help="Start persistent local conversation chat")
    return parser


class _UnavailableCredentialVault:
    """Metadata-only vault placeholder; every secret operation fails closed."""
    def set_secret(self, reference: str, secret: str) -> None:
        raise RuntimeError("OS credential vault is unavailable")
    def get_secret(self, reference: str) -> str | None:
        raise RuntimeError("OS credential vault is unavailable")
    def delete_secret(self, reference: str) -> None:
        raise RuntimeError("OS credential vault is unavailable")


def _default_profile_store(config: dict, *, create: bool):
    path = data_root() / ".yuki" / "provider_profiles.sqlite3"
    if not create and not path.exists():
        return None
    try:
        vault = OSKeyringVault()
    except Exception:
        vault = _UnavailableCredentialVault()
    return ProviderProfileStore(path, vault)


def _local_profile_owner(config: dict) -> str:
    return load_or_create_local_owner_id(config.get("workspace"))


def _resolve_cli_provider(config, provider_factory, store_factory, owner_factory):
    store = store_factory(config, create=False)
    if store is None:
        return provider_factory(config), None, None, None
    try:
        owner_id = owner_factory(config)
        manager = ProviderManager(config)
        manager.load_persisted_profiles(store, owner_id)
        selected = store.selected_profile(owner_id)
        if selected is None:
            return provider_factory(config), store, owner_id, manager
        result = manager.select(selected)
        if not result.startswith("Selected provider:"):
            raise RuntimeError("saved provider profile could not be selected")
        adapter = manager.create_selected_adapter()
        if adapter is None or adapter.is_available() is not True:
            raise RuntimeError("saved provider profile credential is unavailable")
        return adapter, store, owner_id, manager
    except Exception:
        store.close()
        raise


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
        except Exception as exc:
            output_fn(f"Yuki: Conversation failed ({type(exc).__name__}).")
            return 4



def _run_provider_profile_command(args, *, config, store_factory, owner_factory,
                                  secret_input_fn, output_fn) -> int:
    """Handle profile lifecycle before provider initialization is attempted."""
    store = None
    try:
        create = args.profile_action == "add"
        store = store_factory(config, create=create)
        if store is None:
            output_fn("Yuki: Provider profile storage is not initialized.")
            return 3
        owner_id = owner_factory(config)
        if args.profile_action == "add":
            secret = secret_input_fn("Provider API key (input hidden): ")
            profile = store.save(
                owner_id=owner_id, name=args.name, base_url=args.endpoint,
                model=args.model, allowed_host=args.approved_host, api_key=secret,
            )
            manager = ProviderManager(config)
            manager.load_persisted_profiles(store, owner_id)
            selection = manager.select(profile.name)
            adapter = manager.create_selected_adapter() if selection.startswith("Selected provider:") else None
            try:
                ready = (
                    store.credential_available(owner_id, profile.name) is True
                    and adapter is not None and adapter.is_available() is True
                )
            except Exception:
                ready = False
            if not ready:
                # If the vault is locked, deletion intentionally fails closed and
                # preserves metadata so a transient OS-vault outage is recoverable.
                try:
                    store.delete(owner_id, profile.name)
                except Exception:
                    pass
                output_fn("Yuki: The credential could not be verified by the OS vault and adapter.")
                return 3
            _write_json({"profile": {
                "name": profile.name, "base_url": profile.base_url,
                "model": profile.model, "approved_host": profile.allowed_host,
                "credential_configured": True,
            }}, output_fn)
            return 0
        if args.profile_action == "list":
            selected = store.selected_profile(owner_id)
            profiles = []
            for profile in store.list_profiles(owner_id):
                profiles.append({
                    "name": profile.name, "base_url": profile.base_url,
                    "model": profile.model, "approved_host": profile.allowed_host,
                    "credential_configured": store.credential_available(owner_id, profile.name),
                    "selected": profile.name == selected,
                })
            _write_json({"profiles": profiles, "active_profile": selected}, output_fn)
            return 0
        if args.profile_action == "select":
            manager = ProviderManager(config)
            manager.load_persisted_profiles(store, owner_id)
            selection = manager.select(args.name)
            adapter = manager.create_selected_adapter() if selection.startswith("Selected provider:") else None
            if adapter is None or adapter.is_available() is not True:
                output_fn("Yuki: Provider profile credential or adapter is unavailable; selection was not changed.")
                return 3
            store.select_profile(owner_id, args.name)
            _write_json({"active_profile": args.name}, output_fn)
            return 0
        if args.profile_action == "verify":
            manager = ProviderManager(config)
            manager.load_persisted_profiles(store, owner_id)
            selection = manager.select(args.name)
            adapter = manager.create_selected_adapter() if selection.startswith("Selected provider:") else None
            ready = adapter is not None and adapter.is_available() is True
            _write_json({
                "profile": args.name, "local_ready": bool(ready),
                "network_checked": False,
            }, output_fn)
            return 0 if ready else 3
        if args.profile_action == "remove":
            removed = store.delete(owner_id, args.name)
            if not removed:
                output_fn("Yuki: Provider profile was not found.")
                return 1
            _write_json({"removed": args.name}, output_fn)
            return 0
        return 2
    except (ValueError, KeyError):
        output_fn("Yuki: Provider profile request is invalid or the profile was not found.")
        return 2
    except Exception as exc:
        output_fn(f"Yuki could not manage the provider profile ({type(exc).__name__}).")
        return 2
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                output_fn("Yuki: Provider profile storage could not close cleanly.")

def run_cli(
    argv: Sequence[str] | None = None,
    *,
    config_loader: Callable = load_config,
    provider_factory: Callable = create_provider,
    readiness_builder: Callable = build_readiness_report,
    conversation_runtime_factory: Callable = build_local_conversation_runtime,
    profile_store_factory: Callable = _default_profile_store,
    owner_id_factory: Callable = _local_profile_owner,
    secret_input_fn: Callable[[str], str] = getpass.getpass,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], object] = print,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "provider-profile":
        try:
            config = config_loader()
        except Exception as exc:
            output_fn(f"Yuki could not load configuration ({type(exc).__name__}).")
            return 2
        return _run_provider_profile_command(
            args, config=config, store_factory=profile_store_factory,
            owner_factory=owner_id_factory, secret_input_fn=secret_input_fn,
            output_fn=output_fn,
        )

    if args.command == "doctor":
        report = readiness_builder().to_dict()
        _write_json(report, output_fn)
        return 0

    if args.command == "chat":
        runtime = None
        profile_store = None
        try:
            config = config_loader()
            selected_provider, profile_store, owner_id, manager = _resolve_cli_provider(
                config, provider_factory, profile_store_factory, owner_id_factory,
            )
            if manager is not None and profile_store is not None and profile_store.selected_profile(owner_id):
                runtime = conversation_runtime_factory(
                    config, provider_factory=lambda _config: selected_provider,
                )
            else:
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
            if profile_store is not None:
                try:
                    profile_store.close()
                except Exception:
                    output_fn("Yuki: Provider profile storage could not close cleanly.")

    try:
        config = config_loader()
        provider, profile_store, owner_id, manager = _resolve_cli_provider(
            config, provider_factory, profile_store_factory, owner_id_factory,
        )
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
            "profile_selected": bool(profile_store is not None and profile_store.selected_profile(owner_id)),
        }, output_fn)
        if profile_store is not None:
            profile_store.close()
        return 0 if available else 3

    if args.command == "ask":
        answer, status = _safe_ask(provider, args.prompt)
        output_fn(answer)
        if profile_store is not None:
            profile_store.close()
        return status

    if profile_store is not None:
        profile_store.close()
    return 2


def main() -> int:
    try:
        return run_cli()
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
