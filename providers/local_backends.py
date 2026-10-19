"""Local OpenAI-compatible backend presets for vLLM, llama.cpp, LM Studio, and MLX."""
from __future__ import annotations
from providers.openai_tool_calling import OpenAIToolCallingProvider

_PRESETS={
    "vllm":"http://127.0.0.1:8000/v1",
    "llama_cpp":"http://127.0.0.1:8080/v1",
    "lm_studio":"http://127.0.0.1:1234/v1",
    "mlx":"http://127.0.0.1:8000/v1",
}
def create_local_tool_provider(backend: str, model: str, *, base_url: str | None=None, timeout: int=60):
    if backend not in _PRESETS: raise ValueError("Unsupported local backend")
    url=base_url or _PRESETS[backend]
    # Local endpoints may use HTTP, but only on loopback; remote endpoints must use HTTPS.
    from urllib.parse import urlsplit
    import ipaddress
    parts=urlsplit(url)
    host=parts.hostname or ""
    try: loopback=host=="localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError: loopback=False
    if not ((parts.scheme=="http" and loopback) or parts.scheme=="https") or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Local backend must use loopback HTTP or HTTPS")
    return OpenAIToolCallingProvider(model,api_key_env="YUKI_LOCAL_BACKEND_API_KEY",base_url=url,timeout=timeout,allow_local_http=True)
