"""Thin CLI entry point for Yuki's shared provider and readiness services.

This first CLI slice intentionally offers chat and diagnostics only. It does not
execute tools; task execution remains in the guarded interactive runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence

from core.config import load_config
from core.readiness import build_readiness_report
from providers.factory import create_provider
from yuki_agent import YukiAgent


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
        agent = YukiAgent(provider)
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
        output_fn(agent.ask(args.prompt))
        return 0 if agent.status() == "Provider is ready." else 3

    if args.command == "chat":
        output_fn("Yuki chat. Type /quit to exit. Tool execution is disabled in this CLI mode.")
        while True:
            try:
                message = input_fn("You> ")
            except (EOFError, KeyboardInterrupt):
                output_fn("")
                return 0
            if message.strip().lower() in {"/quit", "/exit"}:
                return 0
            if not message.strip():
                continue
            output_fn("Yuki> " + agent.ask(message))


def main() -> int:
    try:
        return run_cli()
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
