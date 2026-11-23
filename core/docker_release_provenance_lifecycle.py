"""Bind Docker's started lifecycle event to signed release-record publication.

The executor's started event contains a generated container name, not the
daemon-assigned container ID. This adapter resolves that name through the
trusted, read-only Docker Engine API, validates ownership/image labels, and
publishes release provenance before forwarding the event to downstream code.
"""
from __future__ import annotations

import re
from typing import Callable, Mapping
from urllib.parse import quote

from core.host_runtime_inventory import RuntimeInventoryError

_CONTAINER_NAME = re.compile(r"^yuki-[0-9a-f]{32}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}$")


def build_docker_release_record_lifecycle_callback(
    *,
    docker_api,
    release_record_publisher: Callable[[str, str, str, str, str], object],
    task_id: str,
    worker_id: str,
    image_ref: str,
    spec_id: str,
    downstream_callback: Callable[[str, str, object], object] | None = None,
) -> Callable[[str, str, object], object]:
    """Publish a per-execution release record before forwarding started.

    `docker_api` must be the operator-owned DockerEngineAPI instance restricted
    to GET-only inspect endpoints. The publisher must be the host-owned
    build_application_release_record_publisher callback. No task/container
    environment or workspace data is read here.
    """
    if not callable(getattr(docker_api, "get_json", None)):
        raise TypeError("docker_api must expose the trusted get_json inspect interface.")
    if not callable(release_record_publisher):
        raise TypeError("release_record_publisher must be callable.")
    if downstream_callback is not None and not callable(downstream_callback):
        raise TypeError("downstream_callback must be callable when provided.")
    if type(task_id) is not str or not task_id or len(task_id) > 200:
        raise ValueError("task_id must be a bounded non-empty string.")
    if type(worker_id) is not str or not worker_id or len(worker_id) > 200:
        raise ValueError("worker_id must be a bounded non-empty string.")
    if type(spec_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", spec_id):
        raise ValueError("spec_id must be a bounded execution-spec identifier.")
    if type(image_ref) is not str or not _IMAGE_REF.fullmatch(image_ref):
        raise ValueError("image_ref must be an immutable digest-pinned image reference.")

    def lifecycle_callback(event: str, container_name: str, result) -> object:
        if event == "task.sandbox.started":
            if type(container_name) is not str or not _CONTAINER_NAME.fullmatch(container_name):
                raise RuntimeInventoryError("started event does not identify an exact Yuki container.")
            inspect = docker_api.get_json(
                "/containers/" + quote(container_name, safe="") + "/json"
            )
            state = inspect.get("State") if isinstance(inspect, Mapping) else None
            config = inspect.get("Config") if isinstance(inspect, Mapping) else None
            labels = config.get("Labels") if isinstance(config, Mapping) else None
            container_id = inspect.get("Id") if isinstance(inspect, Mapping) else None
            expected_labels = {
                "yuki.managed_by": "yuki",
                "yuki.task_id": task_id,
                "yuki.worker_id": worker_id,
                "yuki.profile_id": spec_id,
                "yuki.image_digest": image_ref.rsplit("@", 1)[-1],
            }
            if (
                type(container_id) is not str or not _CONTAINER_ID.fullmatch(container_id)
                or inspect.get("Name") != "/" + container_name
                or not isinstance(state, Mapping) or state.get("Running") is not True
                or not isinstance(config, Mapping) or config.get("Image") != image_ref
                or not isinstance(labels, Mapping)
                or any(labels.get(key) != value for key, value in expected_labels.items())
            ):
                raise RuntimeInventoryError(
                    "Docker container identity, running state, image or ownership labels do not match the execution."
                )
            # Fail closed: the original started event is not forwarded until
            # the signed record is durably published for this daemon ID.
            release_record_publisher(
                task_id, worker_id, container_id, image_ref, spec_id
            )
        if downstream_callback is not None:
            return downstream_callback(event, container_name, result)
        return None

    return lifecycle_callback
