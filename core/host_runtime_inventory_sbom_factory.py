"""Environment-backed factory for the managed host agent's trusted SBOM collector.

All paths and keys come from the root/service-managed systemd environment.
No task request can select a manifest path, key, or publisher identity.
"""
from __future__ import annotations

import importlib
import os

from core.host_runtime_inventory_ipc import load_private_hmac_key
from core.application_release_provenance import (
    ReadOnlyApplicationReleaseProvenanceStore,
    build_host_agent_release_verified_collector,
)
from core.trusted_sbom_inventory import (
    DigestBoundSBOMVerifier,
    ReadOnlySBOMManifestStore,
    build_sbom_inventory_collector,
    build_sbom_runtime_inventory_collector,
)


def build_collector():
    """Return a digest-bound SBOM collector for host_runtime_inventory_service_cli."""
    try:
        service_uid_text = os.environ["YUKI_HOST_INVENTORY_SERVICE_UID"]
        if not service_uid_text.isascii() or not service_uid_text.isdigit():
            raise ValueError
        service_uid = int(service_uid_text)
        directory = os.environ["YUKI_HOST_INVENTORY_SBOM_DIRECTORY"]
        key_path = os.environ["YUKI_HOST_INVENTORY_SBOM_PUBLISHER_KEY_FILE"]
        observer_ref = os.environ["YUKI_HOST_INVENTORY_RUNTIME_OBSERVER"]
    except (KeyError, ValueError) as exc:
        raise RuntimeError("trusted SBOM deployment configuration is incomplete.") from exc

    publisher_key = load_private_hmac_key(key_path, expected_uid=service_uid)
    store = ReadOnlySBOMManifestStore(
        directory=directory,
        expected_uid=service_uid,
    )
    verifier = DigestBoundSBOMVerifier(publisher_key=publisher_key)
    sbom_collector = build_sbom_inventory_collector(manifest_lookup=store, verifier=verifier)
    module_name, separator, attribute = observer_ref.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError("runtime observer must use trusted module:callable syntax.")
    module = importlib.import_module(module_name)
    observer_factory = getattr(module, attribute, None)
    if not callable(observer_factory):
        raise RuntimeError("configured live runtime observer factory is not callable.")
    runtime_observer = observer_factory()
    if not callable(runtime_observer):
        raise RuntimeError("configured live runtime observer factory returned a non-callable observer.")
    inventory_collector = build_sbom_runtime_inventory_collector(
        sbom_collector=sbom_collector,
        runtime_observer=runtime_observer,
    )

    release_settings = (
        "YUKI_HOST_INVENTORY_RELEASE_DIRECTORY",
        "YUKI_HOST_INVENTORY_RELEASE_KEY_FILE",
        "YUKI_APPLICATION_VERSION",
        "YUKI_BUILD_ID",
    )
    configured = [name in os.environ for name in release_settings]
    if any(configured) and not all(configured):
        raise RuntimeError("application release provenance configuration must be complete.")
    if all(configured):
        release_store = ReadOnlyApplicationReleaseProvenanceStore(
            directory=os.environ[release_settings[0]],
            expected_uid=service_uid,
        )
        release_key = load_private_hmac_key(
            os.environ[release_settings[1]],
            expected_uid=service_uid,
        )
        return build_host_agent_release_verified_collector(
            inventory_collector=inventory_collector,
            release_record_lookup=release_store,
            hmac_key=release_key,
            application_version=os.environ[release_settings[2]],
            build_id=os.environ[release_settings[3]],
        )
    return inventory_collector
