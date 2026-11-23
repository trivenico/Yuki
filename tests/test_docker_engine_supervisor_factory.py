import unittest
from unittest.mock import patch

from core.docker_engine_supervisor import DockerEngineAPI
from core.docker_engine_supervisor_factory import build_docker_engine_supervisor_collector


class FakeDockerAPI(DockerEngineAPI):
    def get_json(self, path):
        raise AssertionError("factory test must not perform Docker requests")


class DockerEngineSupervisorFactoryTests(unittest.TestCase):
    def test_release_provenance_is_required_and_injected_by_default(self):
        publisher = lambda *_args: "published"
        gate_releaser = lambda *_args: None
        gate_identity_binder = lambda *_args: None
        with patch(
            "core.host_runtime_inventory_sbom_factory.build_release_record_publisher",
            return_value=publisher,
        ) as build_publisher:
            collector = build_docker_engine_supervisor_collector(
                api=FakeDockerAPI(),
                host_id_provider=lambda: "host-1",
                owner_hash_resolver=lambda _task: "a" * 64,
                evidence_builder=lambda *_args: None,
                gate_releaser=gate_releaser,
                gate_identity_binder=gate_identity_binder,
            )
        build_publisher.assert_called_once_with()
        self.assertIs(collector.release_record_publisher, publisher)
        self.assertIs(collector.gate_releaser, gate_releaser)
        self.assertIs(collector.gate_identity_binder, gate_identity_binder)

    def test_release_provenance_requires_operator_gate_releaser(self):
        with self.assertRaisesRegex(TypeError, "gate_releaser"):
            build_docker_engine_supervisor_collector(
                api=FakeDockerAPI(), host_id_provider=lambda: "host-1",
                owner_hash_resolver=lambda _task: "a" * 64,
                evidence_builder=lambda *_args: None,
            )

    def test_release_provenance_requires_host_gate_identity_binder(self):
        with self.assertRaisesRegex(TypeError, "gate_identity_binder"):
            build_docker_engine_supervisor_collector(
                api=FakeDockerAPI(), host_id_provider=lambda: "host-1",
                owner_hash_resolver=lambda _task: "a" * 64,
                evidence_builder=lambda *_args: None,
                gate_releaser=lambda *_args: None,
            )

    def test_legacy_mode_is_explicit_and_does_not_load_release_keys(self):
        with patch(
            "core.host_runtime_inventory_sbom_factory.build_release_record_publisher",
        ) as build_publisher:
            collector = build_docker_engine_supervisor_collector(
                api=FakeDockerAPI(),
                host_id_provider=lambda: "host-1",
                owner_hash_resolver=lambda _task: "a" * 64,
                evidence_builder=lambda *_args: None,
                require_release_provenance=False,
            )
        build_publisher.assert_not_called()
        self.assertIsNone(collector.release_record_publisher)


    def test_release_publisher_factory_rejects_wrong_service_uid_before_key_access(self):
        from core.host_runtime_inventory_sbom_factory import build_release_record_publisher

        configured = {
            "YUKI_HOST_INVENTORY_SERVICE_UID": "991",
            "YUKI_HOST_INVENTORY_SBOM_DIRECTORY": "/srv/yuki/sbom",
            "YUKI_HOST_INVENTORY_SBOM_PUBLISHER_KEY_FILE": "/etc/yuki/sbom.key",
            "YUKI_HOST_INVENTORY_RUNTIME_OBSERVER": "trusted.observer:build",
            "YUKI_HOST_INVENTORY_RELEASE_DIRECTORY": "/srv/yuki/releases",
            "YUKI_HOST_INVENTORY_RELEASE_KEY_FILE": "/etc/yuki/release.key",
            "YUKI_APPLICATION_VERSION": "1.2.3",
            "YUKI_BUILD_ID": "build-123",
        }
        with patch.dict("os.environ", configured, clear=True), patch(
            "core.host_runtime_inventory_sbom_factory.os.geteuid", return_value=1000
        ), patch(
            "core.host_runtime_inventory_sbom_factory.load_private_hmac_key"
        ) as load_key:
            with self.assertRaisesRegex(RuntimeError, "must run as"):
                build_release_record_publisher()
        load_key.assert_not_called()

    def test_non_boolean_release_policy_is_rejected(self):
        with self.assertRaises(TypeError):
            build_docker_engine_supervisor_collector(
                api=FakeDockerAPI(),
                host_id_provider=lambda: "host-1",
                owner_hash_resolver=lambda _task: "a" * 64,
                evidence_builder=lambda *_args: None,
                require_release_provenance=1,
            )


if __name__ == "__main__":
    unittest.main()
