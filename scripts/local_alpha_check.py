"""Read-only readiness check for running Yuki's local alpha.

Does not install packages, download models, call cloud APIs, or modify config.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check(label: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'ACTION'}  {label}: {detail}")
    return ok


def main() -> int:
    print("Yuki local alpha readiness")
    print(f"Project: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    failures = 0

    failures += not check("Python version", sys.version_info >= (3, 10),
                          "Python 3.10+ required")
    for module in ("yaml",):
        available = importlib.util.find_spec(module) is not None
        failures += not check("Dependency " + module, available,
                              "installed" if available else "install requirements.txt")

    try:
        from core.config import load_config
        config = load_config()
    except Exception as exc:
        print(f"ACTION  Configuration: could not load ({type(exc).__name__}); install requirements and validate config.yaml")
        return 1
    provider = str(config.get("provider", "openai")).strip().lower()
    if provider == "ollama":
        model = str(config.get("ollama_model", "")).strip()
        cli = shutil.which("ollama")
        failures += not check("Ollama CLI", bool(cli),
                              cli or "install Ollama and start its local service")
        if not model:
            failures += 1
            check("Ollama model", False, "set ollama_model in config.yaml")
        else:
            from providers.ollama import OllamaProvider
            try:
                available = OllamaProvider(
                    config.get("ollama_base_url", "http://127.0.0.1:11434"),
                    model,
                ).is_available()
            except (TypeError, ValueError):
                available = False
            failures += not check("Ollama model", available,
                                  model + (" is installed and server responds"
                                           if available else " not confirmed; start Ollama and pull this model"))
    elif provider == "openai":
        configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        failures += not check("OpenAI credentials", configured,
                              "environment key present" if configured
                              else "set OPENAI_API_KEY in your shell")
    elif provider == "gemini":
        cli = shutil.which("gemini")
        failures += not check("Gemini CLI", bool(cli),
                              cli or "install and authenticate Gemini CLI")
    elif provider in {"openai", "gemini"}:
        # CLI availability is not authentication; the acceptance command verifies inference.
        pass
    elif provider == "hybrid":
        model = str(config.get("ollama_model", "")).strip()
        cli = shutil.which("ollama")
        failures += not check("Ollama CLI", bool(cli),
                              cli or "install Ollama and start its local service")
        if not model:
            failures += 1
            check("Hybrid local model", False, "set ollama_model in config.yaml")
        else:
            from providers.ollama import OllamaProvider
            try:
                available = OllamaProvider(
                    config.get("ollama_base_url", "http://127.0.0.1:11434"),
                    model,
                ).is_available()
            except (TypeError, ValueError):
                available = False
            failures += not check("Hybrid local model", available,
                                  model + (" is installed and server responds"
                                           if available else " not confirmed; start Ollama and pull this model"))
        cloud_model = os.environ.get("YUKI_CLOUD_MODEL", "").strip()
        cloud_tasks = os.environ.get("YUKI_CLOUD_TASKS", "").strip()
        if cloud_model and cloud_tasks:
            key_name = os.environ.get("YUKI_CLOUD_API_KEY_ENV", "OPENAI_API_KEY").strip()
            valid_key_name = key_name.isidentifier() and len(key_name) <= 128
            cloud_key = os.environ.get(key_name, "").strip() if valid_key_name else ""
            check("Hybrid cloud opt-in", bool(cloud_key),
                  "cloud routing configured" if cloud_key else
                  "cloud route configured without credentials; local-only mode remains available")
        else:
            check("Hybrid cloud opt-in", True, "local-only mode; no cloud tasks configured")
    else:
        failures += 1
        check("Provider configuration", False, "unsupported provider: " + provider)

    workspace = Path(config.get("workspace", "./yuki_workspace")).expanduser()
    parent = workspace if workspace.exists() else workspace.parent
    writable = parent.exists() and os.access(parent, os.W_OK)
    failures += not check("Workspace", writable,
                          str(workspace) + (" is writable" if writable
                                            else " parent directory is missing or not writable"))

    web_ready = importlib.util.find_spec("uvicorn") is not None
    check("Browser interface dependency", web_ready,
          "uvicorn installed" if web_ready
          else "optional: install requirements-web.txt for browser chat")
    print()
    if failures:
        print(f"NOT READY: {failures} required check(s) need attention.")
        print("Next: resolve ACTION items, then run this check again.")
        return 1
    print("READY FOR A LOCAL SMOKE TEST (not a production-readiness certification).")
    print("Next: python scripts/local_smoke.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
