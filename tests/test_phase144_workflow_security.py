"""Regression tests for the trusted rootless-Docker acceptance workflow.

These checks prevent accidental weakening of the workflow source. They do not
replace GitHub runner-group policy or prove the host runner is actually trusted.
"""
from pathlib import Path
import unittest


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "phase144-rootless-docker.yml"
)


class Phase144WorkflowSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_dispatch_is_not_parameterized_with_an_arbitrary_ref(self):
        self.assertIn("workflow_dispatch:", self.source)
        self.assertNotIn("inputs:", self.source)
        self.assertIn("if: github.ref == 'refs/heads/main'", self.source)
        self.assertIn("ref: main", self.source)

    def test_checkout_does_not_persist_credentials(self):
        self.assertIn("persist-credentials: false", self.source)

    def test_runner_is_explicitly_dedicated_and_time_bounded(self):
        self.assertIn("runs-on: [self-hosted, linux, yuki-rootless-docker]", self.source)
        self.assertIn("timeout-minutes: 15", self.source)

    def test_image_is_admin_configured_and_digest_pinned(self):
        self.assertIn("vars.YUKI_TEST_WORKER_IMAGE", self.source)
        self.assertIn(
            r'docker\\.io/library/python:3\\.11-slim@sha256:[0-9a-f]{64}',
            self.source,
        )
        self.assertNotIn("github.event.inputs", self.source)

    def test_rootless_runtime_is_checked_before_integration(self):
        self.assertIn("Verify rootless Docker daemon", self.source)
        self.assertIn("docker pull \"$WORKER_IMAGE\"", self.source)
        self.assertIn("tests.integration.test_isolated_upgrade_worker_docker", self.source)


if __name__ == "__main__":
    unittest.main()
