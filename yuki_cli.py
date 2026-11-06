"""Thin CLI entry point for Yuki's shared provider and readiness services.

The CLI exposes diagnostics, provider chat, authenticated companion reminders,
and the authenticated local API. Tool execution remains disabled in this mode.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.config import load_config
from core.readiness import build_readiness_report
from providers.factory import create_provider

_LOCAL_API = "http://127.0.0.1:8765"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yuki", description="Yuki AI assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Report local runtime and Ollama readiness")
    sub.add_parser("provider-status", help="Check the configured provider")

    ask = sub.add_parser("ask", help="Send one prompt to the configured provider")
    ask.add_argument("prompt", help="Prompt text")

    sub.add_parser("chat", help="Start a simple interactive chat (no tools executed)")
    reminders = sub.add_parser("reminders", help="Manage owner-scoped companion reminders")
    reminder_commands = reminders.add_subparsers(dest="reminder_command", required=True)
    reminder_list = reminder_commands.add_parser("list", help="List your reminders")
    reminder_list.add_argument("--include-inactive", action="store_true")
    reminder_add = reminder_commands.add_parser("add", help="Create a reminder")
    reminder_add.add_argument("--text", required=True)
    reminder_add.add_argument("--due-at", required=True, help="Timezone-aware ISO 8601 timestamp")
    reminder_cancel = reminder_commands.add_parser("cancel", help="Cancel one of your reminders")
    reminder_cancel.add_argument("reminder_id")

    notifications = sub.add_parser(
        "notifications", help="Inspect notifications delivered to this paired device"
    )
    notification_commands = notifications.add_subparsers(
        dest="notification_command", required=True
    )
    notification_list = notification_commands.add_parser(
        "list", help="List pending device notifications"
    )
    notification_list.add_argument("--after-sequence", type=int, default=0)
    notification_list.add_argument("--limit", type=int, default=50)
    notification_ack = notification_commands.add_parser(
        "ack", help="Acknowledge one device notification"
    )
    notification_ack.add_argument("event_id")

    serve = sub.add_parser(
        "serve",
        help="Start the authenticated loopback API for desktop and local clients",
    )
    serve.add_argument("--host", default="127.0.0.1", help="Loopback IP address only")
    serve.add_argument("--port", type=int, default=8765, help="Local API port")
    return parser


def _write_json(value, output_fn: Callable[[str], object] = print) -> None:
    output_fn(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _reminder_request(path: str, *, method: str = "GET", payload: dict | None = None,
                      token: str | None = None) -> tuple[dict | None, int]:
    """Call only Yuki's fixed loopback API; never accept a caller-controlled URL."""
    session = token or os.environ.get("YUKI_ACCESS_TOKEN")
    if not isinstance(session, str) or not 20 <= len(session) <= 256:
        return {"error": "Set YUKI_ACCESS_TOKEN to a valid Yuki session token."}, 2
    if not path.startswith("/v1/companion/reminders"):
        return {"error": "Invalid reminder endpoint."}, 2
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        _LOCAL_API + path,
        data=body,
        method=method,
        headers={
            "Authorization": "Bearer " + session,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read(24_001)
            if len(raw) > 24_000:
                return {"error": "Yuki API response exceeded the size limit."}, 4
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
            return decoded, 0
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return {"error": "Yuki session is invalid or expired. Sign in again."}, 3
        if exc.code == 404:
            return {"error": "Reminder was not found or is not accessible."}, 3
        if exc.code == 503:
            return {"error": "Yuki reminders are unavailable."}, 3
        return {"error": f"Yuki API request failed (HTTP {exc.code})."}, 4
    except (URLError, TimeoutError, OSError):
        return {"error": "Could not reach Yuki at 127.0.0.1:8765."}, 3
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "Yuki API returned an invalid response."}, 4


def _run_reminders(args, output_fn: Callable[[str], object]) -> int:
    if args.reminder_command == "list":
        query = "?include_inactive=true" if args.include_inactive else ""
        result, status = _reminder_request("/v1/companion/reminders" + query)
    elif args.reminder_command == "add":
        result, status = _reminder_request(
            "/v1/companion/reminders", method="POST",
            payload={"text": args.text, "due_at": args.due_at},
        )
    else:
        if len(args.reminder_id) != 32 or any(c not in "0123456789abcdef" for c in args.reminder_id):
            output_fn("Invalid reminder ID.")
            return 2
        result, status = _reminder_request(
            "/v1/companion/reminders/" + args.reminder_id, method="DELETE",
        )
    _write_json(result, output_fn)
    return status


def _device_request(
    path: str, *, method: str = "GET", token: str | None = None,
) -> tuple[dict | None, int]:
    """Call only the fixed loopback device feed with a paired-device credential."""
    credential = token or os.environ.get("YUKI_DEVICE_TOKEN")
    if not isinstance(credential, str) or not 20 <= len(credential) <= 256:
        return {"error": "Set YUKI_DEVICE_TOKEN to a valid paired-device token."}, 2
    if not (
        re.fullmatch(
            r"/v1/devices/notifications(?:\\?after_sequence=\\d+&limit=\\d+)?",
            path,
        )
        or re.fullmatch(
            r"/v1/devices/notifications/evt_[0-9a-f]{32}/ack", path
        )
    ):
        return {"error": "Invalid device notification endpoint."}, 2
    request = Request(
        _LOCAL_API + path,
        method=method,
        headers={
            "Authorization": "Bearer " + credential,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read(24_001)
            if len(raw) > 24_000:
                return {"error": "Yuki API response exceeded the size limit."}, 4
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
            return decoded, 0
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return {"error": "Paired-device credential is invalid, revoked, or expired."}, 3
        if exc.code == 503:
            return {"error": "Yuki device notifications are unavailable."}, 3
        return {"error": f"Yuki API request failed (HTTP {exc.code})."}, 4
    except (URLError, TimeoutError, OSError):
        return {"error": "Could not reach Yuki at 127.0.0.1:8765."}, 3
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "Yuki API returned an invalid response."}, 4


def _run_notifications(args, output_fn: Callable[[str], object]) -> int:
    if args.notification_command == "list":
        if args.after_sequence < 0 or not 1 <= args.limit <= 100:
            output_fn("Invalid notification cursor or page size (limit must be 1-100).")
            return 2
        path = (
            "/v1/devices/notifications"
            f"?after_sequence={args.after_sequence}&limit={args.limit}"
        )
        result, status = _device_request(path)
    else:
        if (
            len(args.event_id) != 36
            or not args.event_id.startswith("evt_")
            or any(c not in "0123456789abcdef" for c in args.event_id[4:])
        ):
            output_fn("Invalid notification event ID.")
            return 2
        result, status = _device_request(
            "/v1/devices/notifications/" + args.event_id + "/ack",
            method="POST",
        )
    _write_json(result, output_fn)
    return status


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

    if args.command == "serve":
        from api.server import main as server_main
        return server_main(["--host", args.host, "--port", str(args.port)])

    if args.command == "doctor":
        report = readiness_builder().to_dict()
        _write_json(report, output_fn)
        return 0

    if args.command == "reminders":
        return _run_reminders(args, output_fn)

    if args.command == "notifications":
        return _run_notifications(args, output_fn)

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
            answer, status = _safe_ask(provider, message)
            output_fn("Yuki> " + answer)
            if status in {3, 4}:
                return status


def main() -> int:
    try:
        return run_cli()
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
