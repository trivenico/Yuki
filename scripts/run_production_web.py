"""Run a deployment-composed production ASGI runtime under Uvicorn.

The bootstrap factory is trusted deployment code. This module deliberately does
not call scripts.run_web.create_app(), construct identities, load credentials,
run migrations, or invent a production security context.
"""
from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import Any

from core.production_security_runtime import ProductionSecurityRuntime


def _load_factory(spec: str) -> Callable[[], Any]:
    """Load an explicit module:callable deployment bootstrap."""
    if not isinstance(spec, str) or ":" not in spec:
        raise RuntimeError("web_bootstrap_must_be_module_colon_callable")
    module_name, attribute = spec.split(":", 1)
    if (
        not module_name
        or not attribute
        or "." in attribute
        or any(not part.isidentifier() for part in module_name.split("."))
        or not attribute.isidentifier()
    ):
        raise RuntimeError("web_bootstrap_spec_invalid")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise RuntimeError("web_bootstrap_factory_not_callable")
    return factory


def load_production_runtime(factory: Callable[[], Any]) -> Any:
    """Load and verify an owned ASGI runtime before opening a listener."""
    if not callable(factory):
        raise TypeError("web_bootstrap_factory_required")
    runtime = factory()
    try:
        if not callable(runtime):
            raise TypeError("web_bootstrap_must_return_asgi_runtime")
        security_runtime = getattr(runtime, "production_security_runtime", None)
        if not isinstance(security_runtime, ProductionSecurityRuntime):
            raise TypeError("web_bootstrap_must_return_owned_production_security_runtime")
        if not callable(getattr(runtime, "close", None)):
            raise TypeError("web_runtime_close_required")
        security_runtime.verify_readiness()
        return runtime
    except Exception:
        close = getattr(runtime, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        raise


def _loopback_host(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    host = value.strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def run_web_service(
    runtime: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    server_runner: Callable[..., Any] | None = None,
) -> int:
    """Serve one worker on loopback; ASGI lifespan owns graceful shutdown."""
    if not callable(runtime):
        raise TypeError("production_asgi_runtime_required")
    if not _loopback_host(host):
        raise ValueError("production_web_listener_must_bind_loopback")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("production_web_port_out_of_range")
    if server_runner is None:
        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError("install_requirements_web_for_production_asgi_service") from exc
        server_runner = uvicorn.run
    try:
        server_runner(
            runtime,
            host=host,
            port=port,
            lifespan="on",
            workers=1,
            proxy_headers=False,
            server_header=False,
        )
        return 0
    finally:
        # ConversationRuntime.close() is idempotent. This covers failures before
        # Uvicorn completes its ASGI lifespan shutdown as well.
        runtime.close()


def main() -> int:
    if os.environ.get("YUKI_ENVIRONMENT", "").strip().lower() != "production":
        raise SystemExit("YUKI_ENVIRONMENT=production_required")
    host = os.environ.get("YUKI_WEB_HOST", "127.0.0.1").strip()
    if not _loopback_host(host):
        raise SystemExit("production_web_service_binds_loopback_only")
    try:
        port = int(os.environ.get("YUKI_WEB_PORT", "8000"))
    except ValueError as exc:
        raise SystemExit("YUKI_WEB_PORT_must_be_integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("YUKI_WEB_PORT_out_of_range")
    factory_spec = os.environ.get("YUKI_WEB_BOOTSTRAP", "").strip()
    if not factory_spec:
        raise SystemExit("YUKI_WEB_BOOTSTRAP_module_colon_callable_required")

    runtime = load_production_runtime(_load_factory(factory_spec))
    return run_web_service(runtime, host=host, port=port)


if __name__ == "__main__":
    raise SystemExit(main())
