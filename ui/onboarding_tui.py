"""Small dependency-free interactive onboarding wizard."""
from __future__ import annotations

import getpass
import os
from urllib.parse import urlsplit

from core.onboarding import PROVIDERS, save_config

def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default

def _yes(prompt: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}

def run_onboarding(*, path=None, verify_provider=True) -> dict:
    print("\nWelcome to Yuki setup")
    print("Secrets are never written to config.json. Set the displayed environment variable in your shell to persist credentials.")
    names = list(PROVIDERS)
    for index, name in enumerate(names, 1):
        print(f"  {index}. {name}")
    while True:
        raw = _ask("Choose provider number", "1")
        try:
            provider = names[int(raw) - 1]
            break
        except (ValueError, IndexError):
            print("Choose a number from the list.")
    meta = PROVIDERS[provider]
    base_url = _ask("Provider base URL", meta.get("default_url", ""))
    model = _ask("Model name", meta.get("default_model", ""))
    key_env = meta.get("key_env", "")
    model_path = None
    if provider in {"llama.cpp-python", "mlx"}:
        model_path = _ask("Local GGUF/model path or MLX model ID", model)
        if not model_path:
            raise ValueError("Native local inference requires a model path or model ID.")
        config_native = True
    else:
        config_native = False
    if meta["kind"] in {"cloud", "custom"}:
        key_env = _ask("Environment variable name for API key", key_env)
        # Ask only to validate that the user has a credential; never echo or persist it.
        if _yes(f"Enter {key_env} now for a one-session connectivity check?", False):
            secret = getpass.getpass(f"{key_env} (input hidden; not saved): ")
            if secret:
                os.environ[key_env] = secret
                print("Credential loaded for this process only. Export it in your shell to use it on future launches.")
    if verify_provider and provider != "mock":
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Provider URL must include http(s) scheme and a host.")
        if meta["kind"] == "cloud" and parsed.scheme != "https":
            raise ValueError("Cloud providers must use HTTPS.")
        print("Endpoint syntax validated. Live provider verification is adapter-specific and has not been run by this wizard.")
    servers = []
    if _yes("Configure an MCP server now?", False):
        while len(servers) < 25:
            name = _ask("MCP server name")
            endpoint = _ask("MCP HTTPS endpoint")
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                print("Rejected: MCP endpoint must be HTTPS without embedded credentials.")
                continue
            allow = _ask("Comma-separated tool names to allow (empty denies all)")
            servers.append({"name": name, "endpoint": endpoint, "allowed_tools": [x.strip() for x in allow.split(",") if x.strip()]})
            if not _yes("Add another MCP server?", False):
                break
    config = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key_env": key_env,
        "mcp_servers": servers,
        "guardrails": {
            "require_tool_approval": True,
            "require_cloud_approval": True,
            "local_first": True,
        },
    }
    if config_native:
        config["model_path"] = model_path
    if provider == "hybrid":
        config["local_provider"] = _ask("Local provider (ollama, llama.cpp, lm-studio)", "ollama").lower()
        config["local_model"] = _ask("Local model name", model)
        config["cloud_provider"] = _ask("Cloud provider (openai, anthropic, deepseek, glm, openai-compatible)", "openai").lower()
        default_key_env = {"openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY","deepseek":"DEEPSEEK_API_KEY","glm":"ZHIPUAI_API_KEY"}.get(config["cloud_provider"], "YUKI_CUSTOM_API_KEY")
        config["cloud_api_key_env"] = _ask("Environment variable name for cloud API key", default_key_env)
        config["cloud_model"] = _ask("Cloud model name", "")
        config["cloud_tasks"] = [x.strip() for x in _ask("Tasks allowed to route to cloud (comma-separated; empty disables cloud routing)", "").split(",") if x.strip()]
    target = save_config(config, path)
    print(f"Configuration saved to {target}")
    print("Tool approval and cloud-transmission approval remain enabled. MCP tools default to deny unless explicitly allow-listed.")
    return config

def main() -> int:
    try:
        run_onboarding()
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; no configuration was saved.")
        return 130
    except (OSError, ValueError) as exc:
        print(f"Setup failed safely: {exc}")
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
