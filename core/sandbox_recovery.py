"""Conservative recovery for Yuki-labelled Docker task containers.

This module never prunes containers. It only removes a candidate after checking
its complete Yuki label set, immutable image reference, generated name, and
current task/worker lease through an injected trusted callback.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER = re.compile(r"^yuki-[0-9a-f]{32}$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,200}@sha256:[0-9a-f]{64}$")


def durable_task_lease_is_active(store, task_id: str, worker_id: str, owner_sha256: str) -> bool:
    """Return lease status only when task, worker, and persisted owner all match."""
    if (
        type(task_id) is not str or not task_id
        or type(worker_id) is not str or not worker_id
        or type(owner_sha256) is not str or not _SHA256.fullmatch(owner_sha256)
    ):
        return False
    run = store.get(task_id)
    if run is None or not isinstance(run.owner_id, str) or not run.owner_id:
        return False
    if hashlib.sha256(run.owner_id.encode("utf-8")).hexdigest() != owner_sha256:
        return False
    if run.state != "running" or run.lease_owner != worker_id or not run.lease_expires_at:
        return False
    try:
        expires = datetime.fromisoformat(run.lease_expires_at)
    except (TypeError, ValueError):
        return False
    if expires.tzinfo is None:
        return False
    return expires > datetime.now(timezone.utc)


@dataclass(frozen=True)
class SandboxRecoveryReport:
    inspected: int
    removed: int
    skipped: int


class DockerOrphanRecovery:
    """Operator-invoked recovery; callback must query durable task/lease state."""

    def __init__(
        self,
        *,
        docker_binary: str,
        docker_host: str,
        task_is_active: Callable[[str, str, str], bool | None],
        timeout_seconds: float = 10.0,
    ) -> None:
        if type(docker_binary) is not str or not docker_binary.startswith("/"):
            raise ValueError("docker_binary must be an absolute path")
        if type(docker_host) is not str or not docker_host.startswith("unix:///"):
            raise ValueError("docker_host must be a local Unix socket endpoint")
        if not callable(task_is_active):
            raise TypeError("task_is_active must be a trusted callback")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0.1 and 60")
        self.docker_binary = docker_binary
        self.docker_host = docker_host
        self.task_is_active = task_is_active
        self.timeout_seconds = float(timeout_seconds)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.docker_binary, *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, check=False,
            timeout=self.timeout_seconds,
            env={"PATH": self.docker_binary.rsplit("/", 1)[0], "LANG": "C.UTF-8", "DOCKER_HOST": self.docker_host},
        )

    @staticmethod
    def _valid_container(item: object) -> tuple[str, str, str, str, str] | None:
        if type(item) is not dict:
            return None
        name = item.get("Name")
        inspected_id = item.get("Id")
        config = item.get("Config")
        state = item.get("State")
        if (
            type(name) is not str or not _CONTAINER.fullmatch(name.lstrip("/"))
            or type(inspected_id) is not str or not re.fullmatch(r"[0-9a-f]{12,64}", inspected_id)
        ):
            return None
        if type(config) is not dict or type(state) is not dict:
            return None
        labels = config.get("Labels")
        image = config.get("Image")
        if type(labels) is not dict or type(image) is not str or not _IMAGE.fullmatch(image):
            return None
        required = {
            "yuki.managed_by", "yuki.task_id", "yuki.worker_id",
            "yuki.owner_sha256", "yuki.profile_id", "yuki.image_digest",
        }
        if not required <= set(labels):
            return None
        task_id, worker_id = labels.get("yuki.task_id"), labels.get("yuki.worker_id")
        owner_sha256 = labels.get("yuki.owner_sha256")
        profile_id = labels.get("yuki.profile_id")
        image_digest = labels.get("yuki.image_digest")
        if (
            labels.get("yuki.managed_by") != "yuki"
            or type(task_id) is not str or not task_id or len(task_id) > 200 or "\x00" in task_id
            or type(worker_id) is not str or not worker_id or len(worker_id) > 200 or "\x00" in worker_id
            or type(owner_sha256) is not str or not _SHA256.fullmatch(owner_sha256)
            or type(profile_id) is not str or not _PROFILE.fullmatch(profile_id)
            or type(image_digest) is not str or not _SHA256.fullmatch(image_digest)
            or image.rsplit("@sha256:", 1)[-1] != image_digest
        ):
            return None
        return name.lstrip("/"), inspected_id, task_id, worker_id, owner_sha256

    def recover(self) -> SandboxRecoveryReport:
        listed = self._run(["ps", "-aq", "--filter", "label=yuki.managed_by=yuki"])
        if listed.returncode != 0:
            raise RuntimeError("Docker container discovery failed; no containers were removed")
        ids = [line.strip() for line in listed.stdout.decode("ascii", errors="strict").splitlines() if line.strip()]
        if len(ids) > 1000 or any(not re.fullmatch(r"[0-9a-f]{12,64}", value) for value in ids):
            raise RuntimeError("Docker returned an invalid or excessive candidate list")
        inspected = removed = skipped = 0
        for container_id in ids:
            inspected += 1
            result = self._run(["inspect", container_id])
            if result.returncode != 0:
                skipped += 1
                continue
            if len(result.stdout) > 1_048_576:
                skipped += 1
                continue
            try:
                payload = json.loads(result.stdout.decode("utf-8", errors="strict"))
            except (ValueError, UnicodeError):
                skipped += 1
                continue
            if type(payload) is not list or len(payload) != 1:
                skipped += 1
                continue
            validated = self._valid_container(payload[0])
            if validated is None or not validated[1].startswith(container_id):
                skipped += 1
                continue
            _name, _inspected_id, task_id, worker_id, owner_sha256 = validated
            try:
                active = self.task_is_active(task_id, worker_id, owner_sha256)
            except Exception:
                skipped += 1
                continue
            # Unknown state is not permission to delete. Only explicit False
            # from the trusted durable task/lease lookup authorizes removal.
            if active is not False:
                skipped += 1
                continue
            deletion = self._run(["rm", "-f", container_id])
            if deletion.returncode == 0:
                removed += 1
            else:
                skipped += 1
        return SandboxRecoveryReport(inspected=inspected, removed=removed, skipped=skipped)
