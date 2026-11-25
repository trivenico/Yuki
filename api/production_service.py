"""Supervised Linux API entry point for a deployment-composed Yuki ASGI runtime.

The deployment factory owns identity, policy, PostgreSQL, durable audit and the
independent checkpoint integration. This module only validates and serves it.
"""
from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import Any

from api.runtime import ConversationRuntime
from core.production_security_runtime import ProductionSecurityRuntime


def load_factory(spec: str) -> Callable[[], Any]:
    """Load an explicit deployment-owned module:callable factory."""
    if not isinstance(spec, str) or ":" not in spec:
        raise RuntimeError("api_bootstrap_must_be_module_colon_callable")
    module_name, attribute = spec.split(":", 1)
    if not module_name or not attribute or "." in attribute:
        raise RuntimeError("api_bootstrap_spec_invalid")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise RuntimeError("api_bootstrap_factory_not_callable")
    return factory


def load_production_app(factory: Callable[[], Any]) -> ConversationRuntime:
    """Require the existing ASGI runtime and its owned production security stack."""
    if not callable(factory):
        raise TypeError("api_bootstrap_factory_required")
    app = factory()
    if not isinstance(app, ConversationRuntime):
        raise TypeError("api_bootstrap_must_return_conversation_runtime")
    try:
        security_runtime = app.production_security_runtime
        if not isinstance(security_runtime, ProductionSecurityRuntime):
            raise TypeError("api_runtime_must_own_production_security_runtime")
        if app.production_security is not security_runtime.security:
            raise ValueError("api_runtime_security_context_mismatch")
        if not callable(getattr(security_runtime, "verify_readiness", None)):
            raise TypeError("api_security_runtime_readiness_required")
        security_runtime.verify_readiness()
        return app
    except Exception:
        # The factory transferred ownership when it returned the composed
        # runtime. Do not leak database pools when validation/readiness fails.
        try:
            app.close()
        except Exception:
            pass
        raise


def main() -> int:
    if os.environ.get("YUKI_ENVIRONMENT", "").strip().lower() != "production":
        raise SystemExit("YUKI_ENVIRONMENT=production_required")
    factory_spec = os.environ.get("YUKI_API_BOOTSTRAP", "").strip()
    if not factory_spec:
        raise SystemExit("YUKI_API_BOOTSTRAP_module_colon_callable_required")
    host = os.environ.get("YUKI_API_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("api_service_binds_loopback_only_use_private_trusted_proxy")
    try:
        port = int(os.environ.get("YUKI_API_PORT", "8000"))
    except ValueError as exc:
        raise SystemExit("YUKI_API_PORT_must_be_integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("YUKI_API_PORT_out_of_range")

    app = load_production_app(load_factory(factory_spec))
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install the web extra: python -m pip install -r requirements-web.txt") from exc
    uvicorn.run(app, host=host, port=port, lifespan="on", proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
