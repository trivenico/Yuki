"""Construct the configured provider without implicit cloud fallback."""
from __future__ import annotations

import os

from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.multi import MultiProviderRouter
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.oci_iam import OciIamProvider, create_oci_iam_auth
from providers.oci_responses import OciResponsesProvider


def create_provider(config=None, *, environ=None):
    """Build the configured real provider without implicit fallback.

    OpenAI is the default. Hybrid routing keeps general tasks local and only
    routes explicitly allow-listed task names to cloud. API keys are read from
    the process environment, never from YAML or committed files. The mock
    adapter remains available only to tests that call this factory explicitly.
    """
    cfg = dict(config or {})
    env = os.environ if environ is None else environ
    selected = str(cfg.get("provider", "openai")).strip().lower()
    if selected == "mock":
        return MockProvider()
    if selected == "openai":
        model = str(env.get("YUKI_OPENAI_MODEL", cfg.get("openai_model", "gpt-4.1-mini"))).strip()
        return OpenAICompatibleProvider(
            model,
            api_key_env="OPENAI_API_KEY",
            base_url=str(env.get("YUKI_OPENAI_BASE_URL", "https://api.openai.com/v1")),
            environ=env,
        )
    if selected == "oci":
        region = str(env.get("YUKI_OCI_REGION", cfg.get("oci_region", ""))).strip().lower()
        model = str(env.get("YUKI_OCI_MODEL", cfg.get("oci_model", ""))).strip()
        if not region or not all(part.isalnum() or part == "-" for part in region.split("-")):
            raise ValueError("Configure YUKI_OCI_REGION with a valid OCI region identifier.")
        project = str(env.get("YUKI_OCI_PROJECT_OCID", cfg.get("oci_project_ocid", ""))).strip()
        if not model:
            raise ValueError("Configure YUKI_OCI_MODEL with a model enabled in your OCI region.")
        if not project.startswith("ocid1.generativeaiproject."):
            raise ValueError("Configure YUKI_OCI_PROJECT_OCID with your OCI Generative AI project OCID.")
        host = f"inference.generativeai.{region}.oci.oraclecloud.com"
        return OpenAICompatibleProvider(
            model,
            api_key_env="OCI_GENAI_API_KEY",
            base_url=f"https://{host}/openai/v1",
            allowed_hosts={host},
            additional_headers={"openai-project": project},
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
            environ=env,
        )
    if selected == "oci_iam":
        region = str(env.get("YUKI_OCI_REGION", cfg.get("oci_region", ""))).strip().lower()
        model = str(env.get("YUKI_OCI_MODEL", cfg.get("oci_model", ""))).strip()
        project = str(env.get("YUKI_OCI_PROJECT_OCID", cfg.get("oci_project_ocid", ""))).strip()
        if not region or not all(part.isalnum() or part == "-" for part in region.split("-")):
            raise ValueError("Configure YUKI_OCI_REGION with a valid OCI region identifier.")
        if not model:
            raise ValueError("Configure YUKI_OCI_MODEL with a model enabled in your OCI region.")
        if not project.startswith("ocid1.generativeaiproject."):
            raise ValueError("Configure YUKI_OCI_PROJECT_OCID with your OCI Generative AI project OCID.")
        mode = str(env.get("YUKI_OCI_AUTH_MODE", cfg.get("oci_auth_mode", "resource_principal"))).strip().lower()
        auth = create_oci_iam_auth(
            mode,
            config_file=env.get("YUKI_OCI_CONFIG_FILE", cfg.get("oci_config_file")),
            profile_name=str(env.get("YUKI_OCI_PROFILE", cfg.get("oci_profile", "DEFAULT"))),
        )
        return OciIamProvider(
            model,
            region=region,
            project_ocid=project,
            auth=auth,
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
        )
    if selected == "oci_responses":
        region = str(env.get("YUKI_OCI_REGION", cfg.get("oci_region", ""))).strip().lower()
        model = str(env.get("YUKI_OCI_MODEL", cfg.get("oci_model", ""))).strip()
        project = str(env.get("YUKI_OCI_PROJECT_OCID", cfg.get("oci_project_ocid", ""))).strip()
        if not region or not all(part.isalnum() or part == "-" for part in region.split("-")):
            raise ValueError("Configure YUKI_OCI_REGION with a valid OCI region identifier.")
        if not model:
            raise ValueError("Configure YUKI_OCI_MODEL with a model enabled in your OCI region.")
        if not project.startswith("ocid1.generativeaiproject."):
            raise ValueError("Configure YUKI_OCI_PROJECT_OCID with your OCI Generative AI project OCID.")
        mode = str(env.get("YUKI_OCI_AUTH_MODE", cfg.get("oci_auth_mode", "resource_principal"))).strip().lower()
        auth = create_oci_iam_auth(
            mode,
            config_file=env.get("YUKI_OCI_CONFIG_FILE", cfg.get("oci_config_file")),
            profile_name=str(env.get("YUKI_OCI_PROFILE", cfg.get("oci_profile", "DEFAULT"))),
        )
        return OciResponsesProvider(
            model,
            region=region,
            project_ocid=project,
            auth=auth,
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
        )
    if selected == "multi":
        providers = {}
        local_model = str(env.get("YUKI_OLLAMA_MODEL", cfg.get("ollama_model", ""))).strip()
        if local_model:
            providers["local"] = OllamaProvider(
                base_url=str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")),
                model=local_model,
            )
        if str(env.get("YUKI_OCI_MODEL", cfg.get("oci_model", ""))).strip():
            oci_mode = str(env.get("YUKI_OCI_AUTH_MODE", cfg.get("oci_auth_mode", "api_key"))).strip().lower()
            oci_api_mode = str(env.get("YUKI_OCI_API_MODE", cfg.get("oci_api_mode", "chat_completions"))).strip().lower()
            if oci_mode != "api_key" and oci_api_mode == "responses":
                oci_provider_mode = "oci_responses"
            else:
                oci_provider_mode = "oci_iam" if oci_mode != "api_key" else "oci"
            providers["oci_managed"] = create_provider({**cfg, "provider": oci_provider_mode}, environ=env)
        self_hosted_model = str(env.get("YUKI_SELF_HOSTED_MODEL", cfg.get("self_hosted_model", ""))).strip()
        if self_hosted_model:
            base_url = str(env.get("YUKI_SELF_HOSTED_BASE_URL", cfg.get("self_hosted_base_url", ""))).strip()
            allowed_hosts = {
                item.strip().lower().rstrip(".")
                for item in str(env.get("YUKI_CLOUD_ALLOWED_HOSTS", "")).split(",")
                if item.strip()
            }
            if not base_url or not allowed_hosts:
                raise ValueError(
                    "Self-hosted inference requires YUKI_SELF_HOSTED_BASE_URL and "
                    "an exact hostname in YUKI_CLOUD_ALLOWED_HOSTS."
                )
            key_env = str(env.get("YUKI_SELF_HOSTED_API_KEY_ENV", "YUKI_SELF_HOSTED_API_KEY")).strip()
            providers["self_hosted"] = OpenAICompatibleProvider(
                self_hosted_model,
                api_key_env=key_env,
                base_url=base_url,
                allowed_hosts=allowed_hosts,
                timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
                environ=env,
            )
        routes = cfg.get("provider_routes", {})
        default_route = str(cfg.get("provider_default_route", "oci_managed")).strip().lower()
        return MultiProviderRouter(providers, routes=routes, default_route=default_route)
    if selected == "gemini":
        return GeminiCLIProvider(
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
            max_prompt_chars=cfg.get("gemini_max_prompt_chars", 20000),
            max_output_chars=cfg.get("gemini_max_output_chars", 50000),
        )
    if selected not in {"ollama", "hybrid"}:
        raise ValueError("provider must be one of: openai, oci, oci_iam, oci_responses, ollama, gemini, hybrid, multi (mock is test-only).")
    local = OllamaProvider(
        base_url=str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")),
        model=str(cfg.get("ollama_model", env.get("YUKI_OLLAMA_MODEL", ""))),
    )
    if selected == "ollama":
        return local
    cloud_model = str(env.get("YUKI_CLOUD_MODEL", "")).strip()
    cloud = None
    if cloud_model:
        cloud = OpenAICompatibleProvider(
            cloud_model,
            api_key_env=str(env.get("YUKI_CLOUD_API_KEY_ENV", "OPENAI_API_KEY")),
            base_url=str(env.get("YUKI_CLOUD_BASE_URL", "https://api.openai.com/v1")),
        )
    raw_tasks = str(env.get("YUKI_CLOUD_TASKS", "")).strip()
    cloud_tasks = [item.strip() for item in raw_tasks.split(",") if item.strip()]
    return HybridProvider(local, cloud, cloud_tasks=cloud_tasks)
