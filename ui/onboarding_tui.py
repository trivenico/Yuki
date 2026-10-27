"""Small dependency-free interactive onboarding wizard."""
from __future__ import annotations

import getpass
import os
from urllib.parse import urlsplit

from core.onboarding import PROVIDERS, save_config
from core.model_catalog import get_provider, list_providers
from core.model_selection import list_installed_ollama_models

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

def provider_choices() -> list[str]:
    """Return production onboarding choices, preferring a real local provider."""
    return ["ollama"] + [name for name in PROVIDERS if name != "ollama"]


def run_onboarding(*, path=None, verify_provider=True) -> dict:
    print("\nWelcome to Yuki setup")
    print("Secrets are never written to config.json. Set the displayed environment variable in your shell to persist credentials.")
    kind = _ask("Choose model hosting: 1) local  2) cloud  3) hybrid", "1")
    if kind not in {"1", "2", "3"}:
        raise ValueError("Choose 1 for local, 2 for cloud, or 3 for hybrid.")
    if kind == "3":
        provider = "hybrid"
        meta = PROVIDERS[provider]
        base_url = meta.get("default_url", "")
        model = "local-model"
    else:
        provider_kind = "local" if kind == "1" else "cloud"
        choices = [item for item in list_providers(provider_kind) if item["id"] in PROVIDERS]
        names = [item["id"] for item in choices]
        for index, item in enumerate(choices, 1):
            print(f"  {index}. {item['label']} ({item['id']})")
        while True:
            raw = _ask("Choose provider number", "1")
            try:
                provider = names[int(raw) - 1]
                break
            except (ValueError, IndexError):
                print("Choose a number from the list.")
        meta = PROVIDERS[provider]
        catalog_entry = get_provider(provider)
        base_url = _ask("Provider base URL", meta.get("default_url", ""))
        models = catalog_entry["models"]
        if provider == "ollama":
            installed = list_installed_ollama_models(base_url)
            if installed:
                models = installed
                print("Installed Ollama models:")
            else:
                print("No installed Ollama models discovered; enter a model ID or install one with ollama pull.")
        if models:
            for index, item in enumerate(models, 1):
                print(f"  {index}. {item['label']} — {item['id']} ({item['note']})")
            print(f"  {len(models) + 1}. Enter a custom model ID")
            raw_model = _ask("Choose model number or custom ID", "1")
            try:
                index = int(raw_model)
                model = models[index - 1]["id"] if 1 <= index <= len(models) else _ask("Custom model ID")
            except ValueError:
                model = raw_model
        else:
            model = _ask("Model ID (custom IDs are supported)")
    key_env = meta.get("key_env", "")
    if meta["kind"] in {"cloud", "custom"}:
        key_env = _ask("Environment variable name for API key", key_env)
        # Ask only to validate that the user has a credential; never echo or persist it.
        if _yes(f"Enter {key_env} now for a one-session connectivity check?", False):
            secret = getpass.getpass(f"{key_env} (input hidden; not saved): ")
            if secret:
                os.environ[key_env] = secret
                print("Credential loaded for this process only. Export it in your shell to use it on future launches.")
    if verify_provider and provider not in {"mock", "hybrid"}:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Provider URL must include http(s) scheme and a host.")
        if meta["kind"] in {"cloud", "custom"} and parsed.scheme != "https":
            raise ValueError("Cloud providers must use HTTPS.")
        if meta["kind"] == "local" and parsed.scheme == "http":
            import ipaddress
            try:
                loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise ValueError("Local providers must use loopback HTTP or HTTPS.")
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
    if provider == "hybrid":
        config["local_provider"] = _ask("Local provider (ollama, llama.cpp, lm-studio)", "ollama").lower()
        config["local_model"] = _ask("Local model name", model)
        config["cloud_provider"] = _ask("Cloud provider (openai, gemini_api, nvidia, anthropic, deepseek, glm, openai-compatible)", "gemini_api").lower()
        default_key_env = {"openai":"OPENAI_API_KEY","gemini_api":"GEMINI_API_KEY","nvidia":"NVIDIA_API_KEY","anthropic":"ANTHROPIC_API_KEY","deepseek":"DEEPSEEK_API_KEY","glm":"ZHIPUAI_API_KEY"}.get(config["cloud_provider"], "YUKI_CUSTOM_API_KEY")
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
