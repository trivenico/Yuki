"""Thin CLI entry point for Yuki's shared provider and readiness services.

Use `yuki start` for the full guarded runtime. `yuki desktop` opens a Tk desktop
console that forwards commands to the same guarded runtime, including its existing
approval prompts. The legacy Textual dashboard remains an optional CLI feature.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence

from core.config import load_config
from core.readiness import build_readiness_report
from providers.factory import create_provider
from core.extension_store import install_extension, list_extensions, remove_extension


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yuki", description="Yuki AI assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="Start the full guarded Yuki CLI runtime")
    sub.add_parser("desktop", help="Open the desktop GUI console")
    sub.add_parser("setup", help="Guide first-run local Ollama setup; model downloads require confirmation")
    sub.add_parser("doctor", help="Report local runtime and Ollama readiness")
    sub.add_parser("provider-status", help="Check the configured provider")

    for group, kind in (("skills", "skill"), ("plugins", "plugin")):
        extension = sub.add_parser(group, help=f"Manage installed {kind} packs")
        actions = extension.add_subparsers(dest="extension_action", required=True)
        actions.add_parser("list", help=f"List installed {kind} packs")
        install = actions.add_parser("install", help=f"Install a validated {kind} manifest from a local JSON file")
        install.add_argument("manifest", help="Path to the manifest JSON file")
        remove = actions.add_parser("remove", help=f"Remove an installed {kind} pack by id")
        remove.add_argument("extension_id")

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

    if args.command == "start":
        try:
            import main as integrated_runtime
            integrated_runtime.main()
            return 0
        except Exception as exc:
            output_fn(f"Yuki runtime failed to start ({type(exc).__name__}).")
            return 2

    if args.command == "desktop":
        try:
            from ui.desktop_app import run_desktop
            run_desktop()
            return 0
        except RuntimeError as exc:
            output_fn(str(exc))
            return 2
        except Exception as exc:
            output_fn(f"Yuki desktop failed to start ({type(exc).__name__}).")
            return 2

    if args.command in {"skills", "plugins"}:
        kind = "skill" if args.command == "skills" else "plugin"
        try:
            if args.extension_action == "list":
                _write_json({"installed": list_extensions(kind)}, output_fn)
                return 0
            if args.extension_action == "install":
                result = install_extension(args.manifest, kind)
                _write_json({"installed": result, "note": "Manifest validated; no third-party code was executed."}, output_fn)
                return 0
            removed = remove_extension(args.extension_id, kind)
            _write_json({"removed": removed, "id": args.extension_id}, output_fn)
            return 0 if removed else 1
        except (ValueError, OSError) as exc:
            output_fn(f"Extension operation failed safely ({type(exc).__name__}): {str(exc)[:200]}")
            return 2

    if args.command == "doctor":
        report = readiness_builder().to_dict()
        _write_json(report, output_fn)
        return 0

    if args.command == "setup":
        try:
            config = config_loader()
        except Exception as exc:
            output_fn(f"Yuki could not read configuration ({type(exc).__name__}).")
            return 2
        selected = str(config.get("provider", "ollama")).strip().lower()
        if selected not in {"ollama", "hybrid"}:
            output_fn(f"Configured provider: {selected}. Run 'python yuki_cli.py doctor' to review local readiness.")
            output_fn("No provider or cloud setting was changed.")
            return 0
        model = str(os.environ.get("YUKI_OLLAMA_MODEL", config.get("ollama_model", "llama3.2:3b"))).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", model):
            output_fn("The configured Ollama model name is invalid. Use letters, numbers, dots, underscores, colons, slashes, or hyphens.")
            return 2
        ollama = shutil.which("ollama")
        if not ollama:
            output_fn("Ollama CLI was not found. Install Ollama from https://ollama.com/download, then reopen this terminal.")
            output_fn(f"After installation, run: ollama pull {model}")
            output_fn("Yuki did not install software or download a model automatically.")
            return 3
        try:
            listed = subprocess.run(
                [ollama, "list"], capture_output=True, text=True, timeout=10,
                check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            output_fn(f"Could not inspect installed Ollama models ({type(exc).__name__}).")
            return 3
        if listed.returncode != 0:
            output_fn("Ollama could not list local models. Ensure the Ollama service is running.")
            return 3
        installed = set()
        for line in listed.stdout.splitlines()[1:]:
            fields = line.split()
            if fields:
                installed.add(fields[0])
        if model in installed:
            output_fn(f"Configured model appears installed: {model}. Run 'python yuki_cli.py doctor' to check local readiness.")
            return 0
        output_fn(f"Configured local model is not listed: {model}.")
        try:
            answer = input_fn(f"Download this model with 'ollama pull {model}' now? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            output_fn("Setup cancelled; no model was downloaded.")
            return 1
        if answer.strip().lower() not in {"y", "yes"}:
            output_fn(f"Setup paused. When ready, run: ollama pull {model}")
            return 1
        output_fn(f"Downloading {model}. This may take a while...")
        try:
            pulled = subprocess.run(
                [ollama, "pull", model], timeout=1800, check=False, shell=False,
            )
        except subprocess.TimeoutExpired:
            output_fn("Ollama model download timed out. Check Ollama's status and retry when ready.")
            return 3
        except OSError as exc:
            output_fn(f"Ollama model download could not start ({type(exc).__name__}).")
            return 3
        if pulled.returncode != 0:
            output_fn("Ollama model download failed. Check the Ollama output and retry.")
            return 3
        output_fn(f"Model download completed: {model}. Run 'python yuki_cli.py doctor' next; inference is not yet verified.")
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
