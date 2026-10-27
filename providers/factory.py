"""Construct configured providers; no implicit cloud fallback."""
from __future__ import annotations
import os
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.anthropic import AnthropicProvider
from providers.gemini_api import GeminiAPIProvider
from providers.nvidia_nim import NvidiaNIMProvider
_PRESETS={"openai":("https://api.openai.com/v1","OPENAI_API_KEY"),"deepseek":("https://api.deepseek.com/v1","DEEPSEEK_API_KEY"),"glm":("https://open.bigmodel.cn/api/paas/v4","ZHIPUAI_API_KEY"),"llama.cpp":("http://127.0.0.1:8080/v1",""),"lm-studio":("http://127.0.0.1:1234/v1","")}
def _compatible(model,url,key_env,*,environ,approval_callback=None):
    return OpenAICompatibleProvider(model,api_key_env=key_env or "YUKI_LOCAL_DUMMY_KEY",base_url=url,allow_http_loopback=True,environ=environ,approval_callback=approval_callback)
def create_provider(config=None,*,environ=None,cloud_approver=None):
    cfg=dict(config or {}); env=os.environ if environ is None else environ
    selected=str(cfg.get("provider","ollama")).strip().lower()
    if selected=="mock": return MockProvider()
    model=str(cfg.get("model",cfg.get("ollama_model",env.get("YUKI_OLLAMA_MODEL","")))).strip()
    if selected=="ollama": return OllamaProvider(base_url=str(cfg.get("base_url") or cfg.get("ollama_base_url","http://127.0.0.1:11434")),model=model)
    if selected in {"llama.cpp","llama-cpp","lm-studio"}:
        preset,key=_PRESETS["llama.cpp" if selected in {"llama.cpp","llama-cpp"} else "lm-studio"]
        return _compatible(model,str(cfg.get("base_url") or env.get("YUKI_BASE_URL",preset)),str(cfg.get("api_key_env",key)),environ=env)
    if selected == "gemini_api":
        return GeminiAPIProvider(model or "gemini-2.5-flash", api_key_env=str(cfg.get("api_key_env") or "GEMINI_API_KEY"), timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)), environ=env)
    if selected == "nvidia":
        return NvidiaNIMProvider(model or "openai/gpt-oss-20b", api_key_env=str(cfg.get("api_key_env") or "NVIDIA_API_KEY"), environ=env, timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)))
    if selected=="anthropic": return AnthropicProvider(model,api_key_env=str(cfg.get("api_key_env") or "ANTHROPIC_API_KEY"),base_url=str(cfg.get("base_url") or "https://api.anthropic.com"),approval_callback=cloud_approver)
    if selected in {"openai","deepseek","glm","openai-compatible","custom"}:
        preset,key=_PRESETS.get(selected,("https://api.openai.com/v1","OPENAI_API_KEY"))
        url=str(cfg.get("base_url") or env.get("YUKI_CLOUD_BASE_URL",preset)); key_env=str(cfg.get("api_key_env",env.get("YUKI_CLOUD_API_KEY_ENV",key)))
        if selected in {"openai-compatible","custom"} and not url.startswith("https://"): raise ValueError("Custom cloud endpoints must use HTTPS.")
        return _compatible(model or str(env.get("YUKI_CLOUD_MODEL","")),url,key_env,environ=env,approval_callback=cloud_approver)
    if selected=="hybrid":
        local_cfg=dict(cfg,provider=str(cfg.get("local_provider","ollama")),model=str(cfg.get("local_model",cfg.get("ollama_model",cfg.get("model","")))))
        local=create_provider(local_cfg,environ=env)
        cloud_name=str(cfg.get("cloud_provider","openai")).lower(); cloud_model=str(cfg.get("cloud_model",env.get("YUKI_CLOUD_MODEL",""))).strip(); cloud=None
        if cloud_model:
            cloud_key = str(cfg.get("cloud_api_key_env") or cfg.get("api_key_env") or "")
            cloud_cfg = dict(cfg, provider=cloud_name, model=cloud_model, ollama_model=cloud_model)
            if cloud_key:
                cloud_cfg["api_key_env"] = cloud_key
            else:
                cloud_cfg.pop("api_key_env", None)
            cloud=create_provider(cloud_cfg,environ=env,cloud_approver=cloud_approver)
        tasks=cfg.get("cloud_tasks",env.get("YUKI_CLOUD_TASKS",""))
        if isinstance(tasks,str): tasks=[v.strip() for v in tasks.split(",") if v.strip()]
        if not isinstance(tasks,(list,tuple,set)): raise ValueError("cloud_tasks must be a list of task names.")
        return HybridProvider(local,cloud,cloud_tasks=tasks,cloud_approver=cloud_approver)
    raise ValueError("provider must be one of: mock, ollama, llama.cpp, lm-studio, openai, gemini_api, nvidia, anthropic, deepseek, glm, openai-compatible, hybrid.")
