"""Operator-configured entry point for the host runtime inventory service.

Run with `python -m core.host_runtime_inventory_service_cli` from an installed
Yuki environment. The collector factory is trusted deployment code selected
by a root/service-managed environment file; it is never task-controlled.
"""
from __future__ import annotations

import importlib
import os
import signal
import sys
from collections.abc import Callable

from core.host_runtime_inventory_ipc import HostRuntimeInventoryAgent, load_private_hmac_key
from core.host_runtime_inventory_service import HostRuntimeInventoryUnixService


def _required_int(name: str) -> int:
    value = os.environ.get(name, "")
    if not value.isascii() or not value.isdigit():
        raise RuntimeError(f"{name} must be a non-negative integer.")
    return int(value)


def _load_collector_factory(reference: str) -> Callable[[], object]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError("collector factory must use module:callable syntax.")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise RuntimeError("configured collector factory is not callable.")
    return factory


def main() -> int:
    if sys.platform != "linux":
        raise RuntimeError("the host runtime inventory service requires Linux SO_PEERCRED.")
    socket_path = os.environ["YUKI_HOST_INVENTORY_SOCKET"]
    key_path = os.environ["YUKI_HOST_INVENTORY_KEY_FILE"]
    source_id = os.environ["YUKI_HOST_INVENTORY_SOURCE_ID"]
    factory_ref = os.environ["YUKI_HOST_INVENTORY_COLLECTOR_FACTORY"]
    service_uid = _required_int("YUKI_HOST_INVENTORY_SERVICE_UID")
    socket_gid = _required_int("YUKI_HOST_INVENTORY_SOCKET_GID")
    client_uid = _required_int("YUKI_HOST_INVENTORY_CLIENT_UID")
    key = load_private_hmac_key(key_path, expected_uid=service_uid)
    collector = _load_collector_factory(factory_ref)()
    if not callable(collector):
        raise RuntimeError("collector factory must return a callable trusted inventory collector.")
    agent = HostRuntimeInventoryAgent(
        source_id=source_id,
        hmac_key=key,
        inventory_collector=collector,
        expected_client_uid=client_uid,
    )
    service = HostRuntimeInventoryUnixService(
        agent=agent,
        socket_path=socket_path,
        service_uid=service_uid,
        socket_gid=socket_gid,
        socket_mode=0o660,
    )
    service.start()

    def stop(_signum, _frame):
        service.close()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        service.serve_forever()
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
