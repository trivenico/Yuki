"""Thin CLI entry point for Yuki's shared provider and readiness services.

This first CLI slice intentionally offers chat and diagnostics only. It does not
execute tools; task execution remains in the guarded interactive runtime.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence

from companion.conversation import ConversationError, ConversationSession
from core.config import load_config
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
) -> int:
    args = build_parser().parse_args(argv)

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
        output_fn("Yuki companion chat. Type /reset to clear this session or /quit to exit. Tool execution is disabled.")
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
                    role = session.set_role(role_name)
                except ValueError:
                    output_fn("Yuki> Unknown role. Available: companion, tutor, interviewer, developer, security, marketing.")
                else:
                    output_fn("Yuki> Interaction style changed to " + role["name"] + ". Permissions are unchanged.")
                continue
            if command == "/reset":
                session.reset()
                output_fn("Yuki> This conversation has been cleared.")
                continue
            if command in {"/help", "/?"}:
                output_fn("Yuki> I can chat with context during this session. Commands: /role NAME, /reset, /quit.")
                continue
            if not message.strip():
                continue
            try:
                reply = session.respond(message)
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
