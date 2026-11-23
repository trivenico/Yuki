import unittest

from core.docker_release_provenance_lifecycle import (
    build_docker_release_record_lifecycle_callback,
)
from core.host_runtime_inventory import RuntimeInventoryError

IMAGE = "registry.example/yuki:stable@sha256:" + "a" * 64
CONTAINER_NAME = "yuki-" + "b" * 32
CONTAINER_ID = "c" * 64


class FakeDockerAPI:
    def __init__(self, inspect=None):
        self.inspect = inspect or {
            "Id": CONTAINER_ID,
            "Name": "/" + CONTAINER_NAME,
            "State": {"Running": True},
            "Config": {
                "Image": IMAGE,
                "Labels": {
                    "yuki.managed_by": "yuki",
                    "yuki.task_id": "task-1",
                    "yuki.worker_id": "worker-1",
                    "yuki.profile_id": "spec-1",
                    "yuki.image_digest": IMAGE.rsplit("@", 1)[-1],
                },
            },
        }
        self.paths = []

    def get_json(self, path):
        self.paths.append(path)
        return self.inspect


class DockerReleaseLifecycleTests(unittest.TestCase):
    def test_publishes_exact_daemon_identity_before_forwarding_started(self):
        api = FakeDockerAPI()
        events = []
        order = []

        def publisher(*args):
            order.append(("publish", args))
            return "record-published"

        def downstream(*args):
            order.append(("downstream", args))
            events.append(args)

        callback = build_docker_release_record_lifecycle_callback(
            docker_api=api, release_record_publisher=publisher,
            task_id="task-1", worker_id="worker-1", image_ref=IMAGE,
            spec_id="spec-1", downstream_callback=downstream,
        )
        callback("task.sandbox.started", CONTAINER_NAME, None)

        self.assertEqual(api.paths, ["/containers/" + CONTAINER_NAME + "/json"])
        self.assertEqual(order[0], (
            "publish", ("task-1", "worker-1", CONTAINER_ID, IMAGE, "spec-1")
        ))
        self.assertEqual(order[1][0], "downstream")
        self.assertEqual(events[0][0], "task.sandbox.started")

    def test_publisher_failure_prevents_started_event_forwarding(self):
        api = FakeDockerAPI()
        forwarded = []
        def fail_publisher(*_args):
            raise RuntimeInventoryError("publish failed")

        callback = build_docker_release_record_lifecycle_callback(
            docker_api=api, release_record_publisher=fail_publisher,
            task_id="task-1", worker_id="worker-1", image_ref=IMAGE,
            spec_id="spec-1",
            downstream_callback=lambda *args: forwarded.append(args),
        )
        with self.assertRaisesRegex(RuntimeInventoryError, "publish failed"):
            callback("task.sandbox.started", CONTAINER_NAME, None)
        self.assertEqual(forwarded, [])

    def test_rejects_container_binding_mismatch_before_publishing(self):
        api = FakeDockerAPI()
        api.inspect["Config"]["Labels"]["yuki.worker_id"] = "other-worker"
        published = []
        callback = build_docker_release_record_lifecycle_callback(
            docker_api=api, release_record_publisher=lambda *args: published.append(args),
            task_id="task-1", worker_id="worker-1", image_ref=IMAGE,
            spec_id="spec-1",
        )
        with self.assertRaises(RuntimeInventoryError):
            callback("task.sandbox.started", CONTAINER_NAME, None)
        self.assertEqual(published, [])

    def test_non_started_events_do_not_publish_but_are_forwarded(self):
        api = FakeDockerAPI()
        published, forwarded = [], []
        callback = build_docker_release_record_lifecycle_callback(
            docker_api=api, release_record_publisher=lambda *args: published.append(args),
            task_id="task-1", worker_id="worker-1", image_ref=IMAGE,
            spec_id="spec-1",
            downstream_callback=lambda *args: forwarded.append(args),
        )
        callback("task.sandbox.completed", CONTAINER_NAME, "result")
        self.assertEqual(published, [])
        self.assertEqual(forwarded, [("task.sandbox.completed", CONTAINER_NAME, "result")])
        self.assertEqual(api.paths, [])

    def test_rejects_invalid_container_id(self):
        inspect = FakeDockerAPI().inspect
        inspect["Id"] = "not-a-container-id"
        api = FakeDockerAPI(inspect)
        published = []
        callback = build_docker_release_record_lifecycle_callback(
            docker_api=api, release_record_publisher=lambda *args: published.append(args),
            task_id="task-1", worker_id="worker-1", image_ref=IMAGE,
            spec_id="spec-1",
        )
        with self.assertRaises(RuntimeInventoryError):
            callback("task.sandbox.started", CONTAINER_NAME, None)
        self.assertEqual(published, [])


if __name__ == "__main__":
    unittest.main()
