"""Interactive, loopback-only first-run launcher for Yuki's browser alpha."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _prompt_secret(label: str) -> str:
    return getpass.getpass(label).strip()


def _provider_ready(provider: str, model: str | None, environ: dict[str, str]) -> bool:
    """Check local provider readiness without sending an inference request."""
    from core.config import load_config
    from providers.factory import create_provider

    config = load_config()
    config["provider"] = provider
    if provider == "ollama":
        config["ollama_model"] = model or ""
    selected = create_provider(config, environ=environ)
    return selected.is_available()


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Return whether the local listener can bind the requested address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((host, port))
        return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "openai"),
                        help="Select provider without an interactive provider prompt")
    parser.add_argument("--model", help="Ollama model name; defaults to config.yaml")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        print("Yuki requires Python 3.10 or newer.")
        return 1
    if not 1 <= args.port <= 65535:
        print("Port must be between 1 and 65535.")
        return 1
    if not _port_available(args.port):
        print(f"Port {args.port} is already in use on 127.0.0.1. Stop the other service or choose another port.")
        return 1

    provider = args.provider or input("Provider [ollama/openai] (default ollama): ").strip().lower() or "ollama"
    if provider not in {"ollama", "openai"}:
        print("Unsupported local-alpha provider.")
        return 1

    env = os.environ.copy()
    env["YUKI_PROVIDER"] = provider
    env["YUKI_WEB_HOST"] = "127.0.0.1"
    env["YUKI_WEB_PORT"] = str(args.port)
    env.pop("YUKI_WEB_ALLOW_PUBLIC_BIND", None)

    if provider == "ollama":
        model = (args.model or input("Installed Ollama model name (e.g. qwen2.5:7b): ").strip())
        if not model or len(model) > 120 or any(ord(ch) < 32 for ch in model):
            print("Enter a valid installed Ollama model name.")
            return 1
        env["YUKI_OLLAMA_MODEL"] = model
        print("Yuki will use the configured loopback Ollama service.")
    else:
        key = _prompt_secret("OpenAI API key (input hidden; leave blank to use existing environment): ")
        if key:
            env["OPENAI_API_KEY"] = key
        if not env.get("OPENAI_API_KEY", "").strip():
            print("An OpenAI API key is required for this provider.")
            return 1

    try:
        ready = _provider_ready(provider, model if provider == "ollama" else None, env)
    except Exception as exc:
        print(f"Provider preflight failed ({type(exc).__name__}); check provider configuration.")
        return 1
    if not ready:
        if provider == "ollama":
            print("Ollama is not responding with the selected model. Start Ollama and install the model, then retry.")
        else:
            print("OpenAI credentials are not configured. Set OPENAI_API_KEY and retry.")
        return 1

    username = input("Create the first Yuki administrator username: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        print("Username must be 3–64 characters: letters, digits, dot, underscore, or hyphen.")
        return 1
    password = _prompt_secret("Create administrator password (minimum 12 characters): ")
    confirmation = _prompt_secret("Confirm administrator password: ")
    if len(password) < 12 or password != confirmation:
        print("Passwords must match and contain at least 12 characters.")
        return 1
    env["YUKI_BOOTSTRAP_ADMIN_USERNAME"] = username
    env["YUKI_BOOTSTRAP_ADMIN_PASSWORD"] = password
    env["YUKI_WEB_HOST"] = "127.0.0.1"

    print()
    print(f"Starting Yuki at http://127.0.0.1:{args.port}")
    print("This process is loopback-only. Press Ctrl+C to stop Yuki.")
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_web.py")],
            cwd=ROOT, env=env, check=False,
        )
        return completed.returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
