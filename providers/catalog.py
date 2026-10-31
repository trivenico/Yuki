"""Safe, local-only provider and model catalog.

This catalog reports configured intent and adapter readiness without making
provider requests, enumerating remote models, or exposing secret values.
"""
from __future__ import annotations
import ipaddress
import os
import re
from urllib.parse import urlsplit

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_CATALOG = (
    {"name":"ollama","label":"Ollama","auth":"local_runtime","privacy":"local_or_configured_endpoint","implemented":True,
     "capabilities":["text","local_inference"],"model_env":"YUKI_OLLAMA_MODEL","key_env":None},
    {"name":"colibri","label":"Colibri local engine","auth":"local_runtime","privacy":"local_runtime","implemented":True,
     "capabilities":["text","local_inference"],"model_env":"YUKI_COLIBRI_MODEL","key_env":None},
    {"name":"gemini","label":"Gemini CLI","auth":"official_cli_login","privacy":"cloud_provider",
     "implemented":True,"capabilities":["text","official_cli"],"model_env":"YUKI_GEMINI_MODEL","key_env":None},
    {"name":"hybrid","label":"Hybrid local/cloud","auth":"local_runtime_and_optional_api_key","privacy":"task_policy",
     "implemented":True,"capabilities":["text","per_task_routing"],"model_env":"YUKI_CLOUD_MODEL","key_env":"YUKI_CLOUD_API_KEY_ENV"},
    {"name":"openai","label":"OpenAI API","auth":"api_key","privacy":"cloud_provider","implemented":True,
     "capabilities":["text","tooling_provider_dependent"],"model_env":"YUKI_OPENAI_MODEL","key_env":"OPENAI_API_KEY"},
    {"name":"anthropic","label":"Anthropic API","auth":"api_key","privacy":"cloud_provider","implemented":False,
     "capabilities":["text","tooling_provider_dependent"],"model_env":"YUKI_ANTHROPIC_MODEL","key_env":"ANTHROPIC_API_KEY"},
    {"name":"deepseek","label":"DeepSeek API","auth":"api_key","privacy":"cloud_provider","implemented":False,
     "capabilities":["text"],"model_env":"YUKI_DEEPSEEK_MODEL","key_env":"DEEPSEEK_API_KEY"},
    {"name":"kimi","label":"Kimi / Moonshot API","auth":"api_key","privacy":"cloud_provider","implemented":False,
     "capabilities":["text","long_context_model_dependent"],"model_env":"YUKI_KIMI_MODEL","key_env":"MOONSHOT_API_KEY"},
    {"name":"minimax","label":"MiniMax API","auth":"api_key","privacy":"cloud_provider","implemented":False,
     "capabilities":["text","multimodal_model_dependent"],"model_env":"YUKI_MINIMAX_MODEL","key_env":"MINIMAX_API_KEY"},
    {"name":"openrouter","label":"OpenRouter","auth":"api_key","privacy":"third_party_gateway","implemented":False,
     "capabilities":["multi_provider_routing"],"model_env":"YUKI_OPENROUTER_MODEL","key_env":"OPENROUTER_API_KEY"},
    {"name":"tokenrouter","label":"TokenRouter","auth":"api_key","privacy":"third_party_gateway","implemented":False,
     "capabilities":["multi_provider_routing"],"model_env":"YUKI_TOKENROUTER_MODEL","key_env":"TOKENROUTER_API_KEY"},
    {"name":"huggingface","label":"Hugging Face Inference","auth":"optional_or_provider_dependent","privacy":"cloud_provider",
     "implemented":False,"capabilities":["open_model_ecosystem","model_discovery"],"model_env":"YUKI_HF_MODEL","key_env":"HF_TOKEN"},
)

def _model(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(value) <= 120 and _MODEL_RE.fullmatch(value) else None

def build_provider_catalog(*, config=None, environ=None, provider_manager=None):
    """Return sanitized catalog metadata; never include credential values."""
    cfg = config if isinstance(config, dict) else {}
    env = os.environ if environ is None else environ
    current = str(cfg.get("provider", "mock")).strip().lower()
    manager = {}
    if provider_manager is not None:
        try:
            manager = {x["name"]: x for x in provider_manager.list_providers()}
        except Exception:
            manager = {}
    result = []
    for entry in _CATALOG:
        item = dict(entry)
        model_env = item.pop("model_env")
        key_env = item.pop("key_env")
        model = _model(env.get(model_env, "")) if model_env else None
        if item["name"] == "ollama":
            model = _model(env.get("YUKI_OLLAMA_MODEL", cfg.get("ollama_model", "")))
        elif item["name"] == "colibri":
            model = _model(env.get("YUKI_COLIBRI_MODEL", ""))
        elif item["name"] == "gemini":
            model = _model(env.get("YUKI_GEMINI_MODEL", ""))
        elif item["name"] == "openai":
            model = _model(env.get("YUKI_OPENAI_MODEL", cfg.get("openai_model", "gpt-4.1-mini")))
        elif item["name"] == "hybrid":
            model = _model(env.get("YUKI_CLOUD_MODEL", ""))
        configured_key = bool(key_env and str(env.get(key_env, "")).strip())
        if item["name"] == "hybrid":
            key_name = str(env.get("YUKI_CLOUD_API_KEY_ENV", "OPENAI_API_KEY")).strip()
            configured_key = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key_name)
                                  and str(env.get(key_name, "")).strip())
        status = manager.get(item["name"], {})
        implemented = bool(status.get("implemented", item["implemented"]))
        if item["name"] in {"anthropic","deepseek","kimi","minimax","openrouter","tokenrouter","huggingface"}:
            implemented = False
        if item["name"] == "mock":
            ready = False
            reason = "test_only_adapter_not_exposed"
        elif item["name"] == "openai":
            ready = bool(model and configured_key)
            reason = "api_key_and_model_configured" if ready else (
                "api_key_not_configured" if not configured_key else "model_not_configured"
            )
        elif item["name"] == "ollama":
            ready = bool(model)
            reason = "model_configured_runtime_not_probed" if ready else "model_not_configured"
            try:
                parsed = urlsplit(str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")).strip())
                host = (parsed.hostname or "").rstrip(".").lower()
                _ = parsed.port  # Reject malformed ports before assigning a privacy class.
                valid_endpoint = (
                    parsed.scheme.lower() in {"http", "https"}
                    and bool(host)
                    and not parsed.username and not parsed.password
                    and not parsed.query and not parsed.fragment
                )
                try:
                    is_loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    is_loopback = host == "localhost"
                item["privacy"] = (
                    "local_runtime" if valid_endpoint and is_loopback
                    else "local_or_configured_endpoint" if valid_endpoint
                    else "unknown"
                )
            except ValueError:
                item["privacy"] = "unknown"
        elif item["name"] == "colibri":
            ready = bool(model)
            reason = "model_configured_runtime_not_probed" if ready else "model_not_configured"
        elif item["name"] == "gemini":
            installed = bool(shutil_which("gemini"))
            ready = installed
            reason = "cli_detected_auth_not_verified" if installed else "cli_not_installed"
        elif item["name"] == "hybrid":
            ready = bool(_model(env.get("YUKI_OLLAMA_MODEL", cfg.get("ollama_model", ""))))
            reason = "local_model_configured_cloud_opt_in_required" if ready else "local_model_not_configured"
        else:
            ready = False
            reason = "adapter_not_implemented"
        result.append({**item, "configured_model": model, "credential_configured": configured_key,
                       "ready": ready, "status": reason,
                       "selected": item["name"] == current,
                       "implemented": implemented})
    return result

def shutil_which(name):
    # Kept in a tiny wrapper so tests can mock command discovery without
    # importing subprocess or executing anything.
    import shutil
    return shutil.which(name)
