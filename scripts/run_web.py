"""Run the Yuki web chat locally.

For public deployments, place this ASGI app behind a TLS-terminating reverse
proxy, configure trusted host/rate-limit policy, and provision secrets securely.
"""
from __future__ import annotations

import os
from pathlib import Path

from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.config import load_config
from core.guarded_tool_runtime import GuardedToolRuntime
from core.device_pairing import DevicePairingService
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter
from providers.factory import create_provider
from providers.runtime_manager import ProviderRuntimeManager


class DenyAllTools:
    """No tools are exposed by the initial web deployment profile."""

    @staticmethod
    def authorize_tool(name: str) -> bool:
        return False


def create_app():
    config = load_config()
    workspace = Path(config["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)

    auth = BuiltInAuthService(str(workspace / "auth.sqlite3"))
    bootstrap_username = os.environ.get("YUKI_BOOTSTRAP_ADMIN_USERNAME", "").strip()
    bootstrap_password = os.environ.get("YUKI_BOOTSTRAP_ADMIN_PASSWORD", "")
    if bootstrap_username or bootstrap_password:
        if not bootstrap_username or not bootstrap_password:
            raise RuntimeError("Set both YUKI_BOOTSTRAP_ADMIN_USERNAME and YUKI_BOOTSTRAP_ADMIN_PASSWORD.")
        try:
            auth.bootstrap_admin(bootstrap_username, bootstrap_password)
        except PermissionError as exc:
            if str(exc) != "Initial administrator already exists.":
                raise

    provider = create_provider(config)
    adapter = ProviderAgentAdapter(provider)
    provider_manager = ProviderRuntimeManager(
        config, adapter, workspace / "provider-selection.json", environ=os.environ,
    )
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, DenyAllTools()))
    agent_loop = AgentExecutionLoop(
        adapter,
        protocol,
        stream_model=adapter.stream_model,
        max_model_turns=8,
        max_tool_calls=1,
        max_duration_seconds=120,
    )
    inference_database = workspace / "inference.sqlite3"
    device_pairing = DevicePairingService(inference_database)
    inference_jobs = InferenceJobService(inference_database, device_pairing)
    income_opportunities = SQLiteIncomeOpportunityStore(
        str(workspace / "income-opportunities.sqlite3")
    )
    return build_conversation_runtime(
        config, auth_service=auth, agent_loop=agent_loop,
        provider_catalog=provider_manager.catalog,
        provider_selector=provider_manager.select,
        device_pairing=device_pairing, inference_jobs=inference_jobs,
        income_opportunities=income_opportunities,
    )


def main():
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install the optional web server: python -m pip install -r requirements-web.txt") from exc

    host = os.environ.get("YUKI_WEB_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("YUKI_WEB_ALLOW_PUBLIC_BIND") != "true":
        raise SystemExit("Public binding is disabled. Set YUKI_WEB_ALLOW_PUBLIC_BIND=true only behind a trusted TLS reverse proxy.")
    port_raw = os.environ.get("YUKI_WEB_PORT", "8000")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise SystemExit("YUKI_WEB_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("YUKI_WEB_PORT must be between 1 and 65535.")
    uvicorn.run(create_app(), host=host, port=port, lifespan="on")


if __name__ == "__main__":
    main()
