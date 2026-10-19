"""Preflight Yuki terminal MVP without printing secrets or changing settings."""
from __future__ import annotations
import os
import sys
from core.config import load_config
from providers.factory import create_provider

def evaluate(config, environ, local_available):
    """Return errors, warnings, and credential-presence facts only."""
    errors, warnings = [], []
    provider = config.get("provider", "mock")
    facts = {"provider": provider, "local_model_configured": bool(config.get("ollama_model")),
             "cloud_model_configured": bool(environ.get("YUKI_CLOUD_MODEL", "").strip()),
             "cloud_key_present": False, "local_provider_available": None}
    if provider not in {"mock", "ollama", "hybrid"}:
        errors.append("YUKI_PROVIDER must be mock, ollama, or hybrid.")
        return errors, warnings, facts
    if provider in {"ollama", "hybrid"}:
        if not facts["local_model_configured"]:
            errors.append("Set YUKI_OLLAMA_MODEL to an installed local Ollama model.")
        else:
            facts["local_provider_available"] = local_available
            if local_available is not True:
                errors.append("The configured local Ollama model is not reachable; check Ollama and its model.")
    else:
        warnings.append("Mock provider is selected; answers are test placeholders, not AI output.")
    if provider == "hybrid":
        key_name = environ.get("YUKI_CLOUD_API_KEY_ENV", "OPENAI_API_KEY")
        if not isinstance(key_name, str) or not key_name.isidentifier():
            errors.append("YUKI_CLOUD_API_KEY_ENV must be a valid environment variable name.")
        else:
            facts["cloud_key_present"] = bool(environ.get(key_name, "").strip())
        cloud_tasks = [x.strip() for x in environ.get("YUKI_CLOUD_TASKS", "").split(",") if x.strip()]
        if cloud_tasks and not facts["cloud_model_configured"]:
            errors.append("YUKI_CLOUD_TASKS is set but YUKI_CLOUD_MODEL is missing.")
        if cloud_tasks and not facts["cloud_key_present"]:
            errors.append("Cloud tasks are allow-listed but the configured API-key environment variable is empty.")
        if not cloud_tasks:
            warnings.append("No cloud task names are allow-listed; cloud routing stays disabled.")
        if facts["cloud_model_configured"] and not facts["cloud_key_present"]:
            warnings.append("Cloud model is configured but its API-key environment variable is empty.")
    return errors, warnings, facts

def main():
    print("Yuki terminal MVP preflight")
    print(f"Python: {sys.version.split()[0]}")
    if sys.version_info < (3, 11):
        print("FAIL: Python 3.11 or newer is recommended for this deployment.")
        return 1
    try:
        config = load_config()
        provider = create_provider(config)
        local_available = provider.is_available() is True if config["provider"] in {"ollama", "hybrid"} else None
        errors, warnings, facts = evaluate(config, os.environ, local_available)
    except Exception as exc:
        print(f"FAIL: configuration/provider check failed ({type(exc).__name__}).")
        return 1
    print("Configured provider: " + str(facts["provider"]))
    print("Local model configured: " + ("yes" if facts["local_model_configured"] else "no"))
    if facts["local_provider_available"] is not None:
        print("Local model reachable: " + ("yes" if facts["local_provider_available"] else "no"))
    print("Cloud model configured: " + ("yes" if facts["cloud_model_configured"] else "no"))
    print("Cloud API key present: " + ("yes" if facts["cloud_key_present"] else "no"))
    for warning in warnings: print("WARN: " + warning)
    for error in errors: print("FAIL: " + error)
    if errors:
        print(f"Preflight failed: {len(errors)} issue(s).")
        return 1
    print("PASS: configuration preflight completed.")
    print("Note: this does not validate the full test suite or production readiness.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())