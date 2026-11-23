"""Operator-owned composition for Docker supervisor runtime evidence.

This factory keeps release-store paths, signing keys, application/build identity,
and the inventory observer in the host deployment environment. Task data cannot
select or override any of those values.
"""
from __future__ import annotations

from typing import Callable

from core.docker_engine_supervisor import DockerEngineAPI, DockerEngineSupervisorCollector


def build_docker_engine_supervisor_collector(
    *,
    api: DockerEngineAPI,
    host_id_provider: Callable[[], str],
    owner_hash_resolver: Callable[[str], str],
    evidence_builder: Callable,
    gate_releaser: Callable | None = None,
    require_release_provenance: bool = True,
) -> DockerEngineSupervisorCollector:
    """Compose the supervisor with a host-owned release publisher by default.

    The process must run as YUKI_HOST_INVENTORY_SERVICE_UID so the immutable
    release store can enforce publisher ownership. Set require_release_provenance
    to False only for an explicitly approved legacy deployment.
    """
    if type(require_release_provenance) is not bool:
        raise TypeError("require_release_provenance must be a boolean.")
    publisher = None
    if require_release_provenance:
        if not callable(gate_releaser):
            raise TypeError("release-provenance deployments require an operator-owned gate_releaser.")
        from core.host_runtime_inventory_sbom_factory import build_release_record_publisher
        publisher = build_release_record_publisher()
    return DockerEngineSupervisorCollector(
        api=api,
        host_id_provider=host_id_provider,
        owner_hash_resolver=owner_hash_resolver,
        evidence_builder=evidence_builder,
        release_record_publisher=publisher,
        gate_releaser=gate_releaser,
    )
