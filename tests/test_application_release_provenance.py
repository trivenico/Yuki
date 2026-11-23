from __future__ import annotations

import copy
import unittest

from core.application_release_provenance import (
    create_application_release_provenance,
    build_release_verified_runtime_inventory_provider,
    verify_application_release_provenance,
)
from core.host_runtime_inventory import RuntimeInventoryError

KEY = b"release-provenance-test-key-material-32-bytes"
IMAGE = "registry.example/yuki@sha256:" + "a" * 64
RUNTIME = {"python": "3.12.4", "nodejs": "22.3.0"}
DEPENDENCIES = {
    "yuki-core": "1.4.0",
    "deb:libssl3": "3.0.13",
    "npm:@scope/tool#app/node_modules/@scope/tool": "2.1.0",
}


class ApplicationReleaseProvenanceTests(unittest.TestCase):
    def create_record(self):
        return create_application_release_provenance(
            application_version="1.4.0",
            build_id="build-20261123.7",
            image_ref=IMAGE,
            task_id="task-17",
            worker_id="worker-2",
            spec_id="spec-9",
            runtime_versions=RUNTIME,
            installed_dependencies=DEPENDENCIES,
            hmac_key=KEY,
        )

    def verify(self, record, **overrides):
        values = {
            "hmac_key": KEY,
            "expected_application_version": "1.4.0",
            "expected_build_id": "build-20261123.7",
            "expected_image_ref": IMAGE,
            "task_id": "task-17",
            "worker_id": "worker-2",
            "spec_id": "spec-9",
            "runtime_versions": RUNTIME,
            "installed_dependencies": DEPENDENCIES,
        }
        values.update(overrides)
        return verify_application_release_provenance(record, **values)

    def test_round_trip_binds_release_execution_and_measured_inventory(self):
        record = self.create_record()
        self.assertEqual(self.verify(record), "sha256:" + "a" * 64)

    def test_rejects_tampered_release_fields_and_signature(self):
        record = self.create_record()
        record["build_id"] = "different-build"
        with self.assertRaises(RuntimeInventoryError):
            self.verify(record)

    def test_rejects_cross_task_worker_or_spec_replay(self):
        record = self.create_record()
        for field, value in (("task_id", "other-task"), ("worker_id", "other-worker"), ("spec_id", "other-spec")):
            changed = copy.deepcopy(record)
            changed[field] = value
            # Re-signing is not available to a caller without the trusted key;
            # this still verifies the signature rejection path for replay attempts.
            with self.assertRaises(RuntimeInventoryError):
                self.verify(changed)

    def test_rejects_runtime_or_dependency_inventory_drift(self):
        record = self.create_record()
        with self.assertRaises(RuntimeInventoryError):
            self.verify(record, runtime_versions={"python": "3.12.5", "nodejs": "22.3.0"})
        with self.assertRaises(RuntimeInventoryError):
            self.verify(record, installed_dependencies={**DEPENDENCIES, "yuki-core": "1.4.1"})

    def test_rejects_unknown_fields_and_boolean_schema_version(self):
        record = self.create_record()
        changed = dict(record, extra="not-allowed")
        with self.assertRaises(RuntimeInventoryError):
            self.verify(changed)
        changed = dict(record, schema_version=True)
        with self.assertRaises(RuntimeInventoryError):
            self.verify(changed)

    def test_rejects_unpinned_image_and_weak_key(self):
        with self.assertRaises(RuntimeInventoryError):
            create_application_release_provenance(
                application_version="1.4.0", build_id="build-1", image_ref="registry/yuki:latest",
                task_id="task", worker_id="worker", spec_id="spec", runtime_versions=RUNTIME,
                installed_dependencies=DEPENDENCIES, hmac_key=KEY,
            )
        with self.assertRaises(RuntimeInventoryError):
            create_application_release_provenance(
                application_version="1.4.0", build_id="build-1", image_ref=IMAGE,
                task_id="task", worker_id="worker", spec_id="spec", runtime_versions=RUNTIME,
                installed_dependencies=DEPENDENCIES, hmac_key=b"short",
            )

    def test_release_verifier_is_wired_as_host_inventory_gate(self):
        from types import SimpleNamespace
        inventory = {"runtime_versions": RUNTIME, "installed_dependencies": DEPENDENCIES}
        record = self.create_record()
        provider = build_release_verified_runtime_inventory_provider(
            inventory_provider=lambda *_: inventory,
            release_record_lookup=lambda *_: record,
            hmac_key=KEY, application_version="1.4.0", build_id="build-20261123.7",
        )
        spec = SimpleNamespace(spec_id="spec-9")
        profile = SimpleNamespace(image=IMAGE)
        inspect = {"Config": {"Image": IMAGE}}
        result = provider("task-17", "worker-2", "a" * 64, inspect, spec, profile)
        self.assertEqual(result, inventory)

    def test_host_inventory_gate_fails_closed_on_missing_or_wrong_execution_record(self):
        from types import SimpleNamespace
        record = self.create_record()
        provider = build_release_verified_runtime_inventory_provider(
            inventory_provider=lambda *_: {"runtime_versions": RUNTIME, "installed_dependencies": DEPENDENCIES},
            release_record_lookup=lambda *_: record,
            hmac_key=KEY, application_version="1.4.0", build_id="build-20261123.7",
        )
        spec = SimpleNamespace(spec_id="spec-9")
        profile = SimpleNamespace(image=IMAGE)
        inspect = {"Config": {"Image": IMAGE}}
        with self.assertRaises(RuntimeInventoryError):
            provider("different-task", "worker-2", "a" * 64, inspect, spec, profile)
        missing = build_release_verified_runtime_inventory_provider(
            inventory_provider=lambda *_: {"runtime_versions": RUNTIME, "installed_dependencies": DEPENDENCIES},
            release_record_lookup=lambda *_: (_ for _ in ()).throw(FileNotFoundError()),
            hmac_key=KEY, application_version="1.4.0", build_id="build-20261123.7",
        )
        with self.assertRaises(RuntimeInventoryError):
            missing("task-17", "worker-2", "a" * 64, inspect, spec, profile)


if __name__ == "__main__":
    unittest.main()
