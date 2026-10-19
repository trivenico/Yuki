"""First-run configuration wizard; secrets are environment references, never persisted values."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Any

DEFAULT_CONFIG = Path.home() / ".yuki" / "config.json"
PROVIDERS = {"mock", "ollama", "llama_cpp", "lm_studio", "vllm", "openai", "anthropic", "deepseek", "glm", "openai_compatible"}

def save_config(config: dict[str, Any], path: Path = DEFAULT_CONFIG) -> None:
    if not isinstance(config, dict): raise ValueError("Config must be an object")
    provider=config.get("provider","mock")
    if provider not in PROVIDERS: raise ValueError("Unsupported provider")
    key_env=config.get("api_key_env")
    if key_env is not None and (not isinstance(key_env,str) or not key_env.isidentifier()):
        raise ValueError("api_key_env must be an environment variable name")
    safe = dict(config)
    for key in ("api_key","secret","password","access_token","refresh_token"):
        if safe.get(key): raise ValueError(f"Do not store {key}; store only a secret environment-variable name")
    body=json.dumps(safe,indent=2,allow_nan=False).encode()
    if len(body)>128*1024: raise ValueError("Config exceeds 128 KiB")
    path=path.expanduser()
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    try: os.chmod(path.parent,0o700)
    except OSError: pass
    fd,tmp=tempfile.mkstemp(prefix=".config-",suffix=".tmp",dir=path.parent)
    try:
        try: os.fchmod(fd,0o600)
        except OSError: pass
        with os.fdopen(fd,"wb") as f:
            f.write(body); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def needs_onboarding(path: Path = DEFAULT_CONFIG) -> bool: return not path.expanduser().is_file()

def run_onboarding(input_fn: Callable[[str],str]=input, *, path: Path=DEFAULT_CONFIG,
                   validator: Callable[[str,dict[str,Any]],bool] | None=None) -> dict[str,Any]:
    if not needs_onboarding(path):
        try: return json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError) as exc: raise RuntimeError("Existing Yuki config is invalid") from exc
    print("Yuki first-run setup. API keys are not written to config; set the named environment variable.")
    provider=input_fn("Provider [mock/ollama/llama_cpp/lm_studio/vllm/openai/anthropic/deepseek/glm/openai_compatible] (mock): ").strip().lower() or "mock"
    if provider not in PROVIDERS: raise ValueError("Unsupported provider")
    model=input_fn("Model name (optional for mock): ").strip()
    base=input_fn("Base URL (blank for provider default): ").strip()
    key_env=None
    if provider in {"openai","anthropic","deepseek","glm","openai_compatible"}:
        key_env=input_fn("Environment variable name for API key (e.g. OPENAI_API_KEY): ").strip()
        if not key_env.isidentifier(): raise ValueError("Invalid environment variable name")
        if validator and not validator(provider,{"model":model,"base_url":base,"api_key_env":key_env}):
            raise RuntimeError("Provider validation failed")
    mcp_enabled=input_fn("Enable MCP integrations? [y/N]: ").strip().lower()=="y"
    mcp_servers=[]
    if mcp_enabled:
        print("MCP servers are registered as HTTPS endpoints; tool execution remains approval-gated.")
        endpoint=input_fn("MCP HTTPS endpoint: ").strip()
        if not endpoint.startswith("https://"): raise ValueError("MCP endpoint must use HTTPS")
        mcp_servers=[{"endpoint":endpoint,"enabled":False,"allowed_tools":[]}]
    config={"version":1,"provider":provider,"model":model,"base_url":base,"api_key_env":key_env,
            "mcp_servers":mcp_servers,"security":{"allow_network":False,"allow_file_write":False,
            "allow_shell":False,"require_approval_for_unknown_tools":True},
            "daemon":{"enabled":False,"poll_seconds":1.0},"messaging":{"enabled":False}}
    save_config(config,path)
    return config
