"""Static capability catalog; not a live model inventory."""
from copy import deepcopy

_CATALOG = {
 "mock":{"implemented":True,"access_mode":"offline","credential_env":[],"privacy":"local","notes":"Deterministic test/development adapter; not a real language model."},
 "ollama":{"implemented":True,"access_mode":"local_runtime","credential_env":[],"privacy":"local_by_default","notes":"Requires a local Ollama server and installed model."},
 "openai_compatible":{"implemented":True,"access_mode":"api_key","credential_env":["YUKI_CLOUD_API_KEY_ENV"],"privacy":"remote_explicit","notes":"Generic HTTPS OpenAI-compatible endpoint; model and endpoint are user-configured."},
 "hybrid":{"implemented":True,"access_mode":"local_first_task_allowlist","credential_env":["YUKI_CLOUD_API_KEY_ENV"],"privacy":"local_default_remote_allowlist","notes":"Only explicitly allow-listed tasks may use the configured cloud adapter."},
 "openai":{"implemented":False,"access_mode":"official_api_or_supported_cli","credential_env":[],"privacy":"not_configured","notes":"No dedicated native adapter is claimed by this catalog entry."},
 "anthropic":{"implemented":False,"access_mode":"official_api_or_supported_cli","credential_env":[],"privacy":"not_configured","notes":"No dedicated native adapter is claimed by this catalog entry."},
 "gemini":{"implemented":False,"access_mode":"official_api_or_supported_cli","credential_env":[],"privacy":"not_configured","notes":"Requires a separately reviewed and tested adapter before selection."},
 "deepseek":{"implemented":False,"access_mode":"official_api","credential_env":[],"privacy":"not_configured","notes":"Provider access and model compatibility must be verified before enabling."},
 "moonshot_kimi":{"implemented":False,"access_mode":"official_api","credential_env":[],"privacy":"not_configured","notes":"Provider access and model compatibility must be verified before enabling."},
 "minimax":{"implemented":False,"access_mode":"official_api","credential_env":[],"privacy":"not_configured","notes":"Provider access and model compatibility must be verified before enabling."},
 "zai_glm":{"implemented":False,"access_mode":"official_api","credential_env":[],"privacy":"not_configured","notes":"Provider access and model compatibility must be verified before enabling."},
 "openrouter":{"implemented":False,"access_mode":"gateway_api_key","credential_env":[],"privacy":"remote_gateway","notes":"Gateway requires user-supplied credentials and explicit remote-routing consent."},
 "tokenrouter":{"implemented":False,"access_mode":"gateway_api_key","credential_env":[],"privacy":"remote_gateway","notes":"Gateway requires user-supplied credentials and explicit remote-routing consent."}
}

def list_providers():
    """Return a defensive copy suitable for status commands or a future UI."""
    return [{"id": key, **deepcopy(value)} for key, value in _CATALOG.items()]

def get_provider(provider_id):
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("Provider identifier is required.")
    key = provider_id.strip().lower()
    if key not in _CATALOG:
        raise ValueError("Unknown provider identifier.")
    return {"id": key, **deepcopy(_CATALOG[key])}

def implemented_provider_ids():
    return tuple(key for key, value in _CATALOG.items() if value["implemented"])
