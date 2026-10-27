"""NVIDIA NIM provider wrapper."""
from providers.openai_compatible import OpenAICompatibleProvider

class NvidiaNIMProvider(OpenAICompatibleProvider):
    def __init__(self, model, *, api_key_env="NVIDIA_API_KEY", environ=None, timeout=45):
        super().__init__(model, api_key_env=api_key_env, base_url="https://integrate.api.nvidia.com/v1", environ=environ, timeout=timeout, allowed_hosts={"integrate.api.nvidia.com"})
