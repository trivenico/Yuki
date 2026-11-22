import json
import unittest
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.sandbox_recovery import DockerOrphanRecovery, durable_task_lease_is_active


class FakeRecovery(DockerOrphanRecovery):
    def __init__(self, *, payload, active=False):
        super().__init__(
            docker_binary="/usr/bin/docker", docker_host="unix:///run/user/1000/docker.sock",
            task_is_active=lambda task_id, worker_id, owner_sha256: active,
        )
        self.payload = payload
        self.calls = []

    def _run(self, args):
        import subprocess
        self.calls.append(args)
        if args[:2] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(args, 0, b"0123456789ab\n", b"")
        if args[:1] == ["inspect"]:
            return subprocess.CompletedProcess(args, 0, json.dumps([self.payload]).encode(), b"")
        if args[:2] == ["rm", "-f"]:
            return subprocess.CompletedProcess(args, 0, b"0123456789ab\n", b"")
        raise AssertionError("unexpected Docker command")


def valid_container():
    digest = "a" * 64
    return {
        "Id": "0123456789ab" + "c" * 52,
        "Name": "/yuki-" + "b" * 32,
        "Config": {
            "Image": "registry.example/yuki-runner@sha256:" + digest,
            "Labels": {
                "yuki.managed_by": "yuki",
                "yuki.task_id": "task-123",
                "yuki.worker_id": "worker-123",
                "yuki.owner_sha256": "c" * 64,
                "yuki.profile_id": "python-review",
                "yuki.image_digest": digest,
            },
        },
        "State": {"Running": True},
    }


class DockerOrphanRecoveryTests(unittest.TestCase):
    def test_removes_only_verified_yuki_container_for_inactive_task(self):
        recovery = FakeRecovery(payload=valid_container(), active=False)
        report = recovery.recover()
        self.assertEqual((report.inspected, report.removed, report.skipped), (1, 1, 0))
        self.assertEqual(recovery.calls[-1][:2], ["rm", "-f"])

    def test_preserves_container_when_task_is_active(self):
        recovery = FakeRecovery(payload=valid_container(), active=True)
        report = recovery.recover()
        self.assertEqual((report.removed, report.skipped), (0, 1))
        self.assertFalse(any(call[:2] == ["rm", "-f"] for call in recovery.calls))

    def test_preserves_container_when_task_state_is_unknown(self):
        recovery = FakeRecovery(payload=valid_container(), active=None)
        report = recovery.recover()
        self.assertEqual((report.removed, report.skipped), (0, 1))

    def test_rejects_container_with_missing_or_mismatched_labels(self):
        payload = valid_container()
        del payload["Config"]["Labels"]["yuki.owner_sha256"]
        recovery = FakeRecovery(payload=payload, active=False)
        report = recovery.recover()
        self.assertEqual((report.removed, report.skipped), (0, 1))

    def test_durable_lease_check_requires_matching_owner_and_worker(self):
        owner_id = "account-123"
        expires = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        run = SimpleNamespace(
            owner_id=owner_id, state="running", lease_owner="worker-123",
            lease_expires_at=expires,
        )
        store = SimpleNamespace(get=lambda task_id: run)
        owner_hash = hashlib.sha256(owner_id.encode()).hexdigest()
        self.assertTrue(durable_task_lease_is_active(store, "task-123", "worker-123", owner_hash))
        self.assertFalse(durable_task_lease_is_active(store, "task-123", "worker-other", owner_hash))
        self.assertFalse(durable_task_lease_is_active(store, "task-123", "worker-123", "0" * 64))

    def test_durable_lease_check_rejects_expired_and_legacy_unowned_tasks(self):
        owner_id = "account-123"
        owner_hash = hashlib.sha256(owner_id.encode()).hexdigest()
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        run = SimpleNamespace(
            owner_id=owner_id, state="running", lease_owner="worker-123",
            lease_expires_at=expired,
        )
        store = SimpleNamespace(get=lambda task_id: run)
        self.assertFalse(durable_task_lease_is_active(store, "task-123", "worker-123", owner_hash))
        run.owner_id = None
        self.assertFalse(durable_task_lease_is_active(store, "task-123", "worker-123", owner_hash))

    def test_rejects_image_digest_mismatch(self):
        payload = valid_container()
        payload["Config"]["Labels"]["yuki.image_digest"] = "d" * 64
        recovery = FakeRecovery(payload=payload, active=False)
        report = recovery.recover()
        self.assertEqual((report.removed, report.skipped), (0, 1))


if __name__ == "__main__":
    unittest.main()
