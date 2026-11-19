"""Production API runtime factory with mandatory durable sandbox recovery wiring.

Deployments that enable durable sandbox jobs must use this factory rather than
calling api.runtime.build_conversation_runtime directly.
"""
from __future__ import annotations

from typing import Any

from api.runtime import build_conversation_runtime
from automation.sandbox_job_composition import build_sandbox_job_recovery


def build_production_conversation_runtime(
    config: dict[str, Any],
    **runtime_dependencies: Any,
):
    """Build the API runtime and bind durable recovery whenever jobs are enabled.

    The sandbox recovery dependency is owned by this factory; callers cannot
    bypass or replace it through runtime_dependencies.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary")
    if "sandbox_job_recovery" in runtime_dependencies:
        raise ValueError("sandbox_job_recovery is managed by the production factory")

    recovery = build_sandbox_job_recovery(config)
    if config.get("sandbox_jobs_enabled", False) and recovery is None:
        raise RuntimeError("durable sandbox jobs are enabled but recovery is unavailable")

    return build_conversation_runtime(
        config,
        sandbox_job_recovery=recovery,
        **runtime_dependencies,
    )
