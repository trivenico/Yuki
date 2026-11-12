"""Thin CLI entry point for Yuki's provider, diagnostics and persistent chat."""
from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable, Sequence

from core.config import load_config
from core.local_cli_runtime import build_local_conversation_runtime
from core.local_annotation_runtime import build_local_annotation_runtime
from core.readiness import build_readiness_report
from providers.factory import create_provider
from ui.annotation_workbench import run_annotation_workbench


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yuki", description="Yuki AI assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Report local runtime and Ollama readiness")
    sub.add_parser("provider-status", help="Check the configured provider")

    ask = sub.add_parser("ask", help="Send one prompt to the configured provider")
    ask.add_argument("prompt", help="Prompt text")

    sub.add_parser("chat", help="Start persistent local conversation chat")
    sub.add_parser("annotation", help="Open the local data annotation workbench")
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
        except Exception as exc:
            output_fn(f"Yuki: Conversation failed ({type(exc).__name__}).")
            return 4


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    config_loader: Callable = load_config,
    provider_factory: Callable = create_provider,
    readiness_builder: Callable = build_readiness_report,
    conversation_runtime_factory: Callable = build_local_conversation_runtime,
    annotation_runtime_factory: Callable = build_local_annotation_runtime,
    annotation_ui_runner: Callable = run_annotation_workbench,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], object] = print,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "doctor":
        report = readiness_builder().to_dict()
        _write_json(report, output_fn)
        return 0

    if args.command == "annotation":
        try:
            config = config_loader()
            runtime = annotation_runtime_factory(config)
        except Exception as exc:
            output_fn(
                f"Yuki could not initialize the annotation workbench ({type(exc).__name__})."
            )
            return 2
        try:
            annotation_ui_runner(runtime.workbench, runtime.owner_id)
            return 0
        except RuntimeError as exc:
            message = str(exc)
            if message.startswith("Install the optional UI dependency"):
                output_fn(message)
            else:
                output_fn("Yuki annotation workbench stopped safely (RuntimeError).")
            return 2
        except Exception as exc:
            output_fn(
                f"Yuki annotation workbench failed safely ({type(exc).__name__})."
            )
            return 4

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

    return 2


def main() -> int:
    try:
        return run_cli()
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
