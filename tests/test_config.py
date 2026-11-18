"""Contract tests for Yuki's centralized runtime configuration loader."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from core.config import load_config


class ConfigContractTests(unittest.TestCase):
    def _load(self, directory, values):
        path = Path(directory) / "config.yaml"
        path.write_text(yaml.safe_dump(values), encoding="utf-8")
        return load_config(path)

    def test_minimal_configuration_resolves_workspace_and_safe_defaults(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            workspace = Path(directory) / "workspace"
            config = self._load(directory, {"workspace": str(workspace)})
            self.assertEqual(config["workspace"], str(workspace.resolve()))
            self.assertEqual(config["mcp_servers"], [])
            self.assertFalse(config["voice_enabled"])
            self.assertFalse(config["conversation_memory_enabled"])
            self.assertTrue(config["firewall_dry_run"])
            self.assertFalse(config["sandbox_execution_enabled"])
            self.assertEqual(config["sandbox_execution_allowed_images"], [])
            self.assertEqual(config["sandbox_execution_worker_id"], "yuki-sandbox-worker")
            self.assertEqual(
                config["sandbox_execution_workspace"],
                str((workspace / "sandbox-execution-workspace").resolve()),
            )

    def test_sandbox_execution_configuration_is_loaded_and_opt_in(self):
        image = "registry.example/yuki@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = self._load(directory, {
                "workspace": directory,
                "sandbox_execution_enabled": True,
                "sandbox_execution_allowed_images": [image],
                "sandbox_execution_worker_id": "worker-01",
                "sandbox_execution_workspace": str(Path(directory) / "isolated"),
            })
            self.assertTrue(config["sandbox_execution_enabled"])
            self.assertEqual(config["sandbox_execution_allowed_images"], [image])
            self.assertEqual(config["sandbox_execution_worker_id"], "worker-01")
            self.assertEqual(config["sandbox_execution_workspace"], str((Path(directory) / "isolated").resolve()))

    def test_sandbox_execution_enable_environment_override_is_supported(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"YUKI_SANDBOX_EXECUTION_ENABLED": "true"}, clear=True
        ):
            config = self._load(directory, {"workspace": directory})
            self.assertTrue(config["sandbox_execution_enabled"])

    def test_rejects_unpinned_sandbox_execution_image(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "digest-pinned"):
                self._load(directory, {
                    "workspace": directory,
                    "sandbox_execution_allowed_images": ["registry.example/yuki:latest"],
                })

    def test_rejects_invalid_sandbox_execution_worker_id(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "simple identifier"):
                self._load(directory, {
                    "workspace": directory,
                    "sandbox_execution_worker_id": "worker id with spaces",
                })

    def test_rejects_non_loopback_plain_http_provider_endpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HTTPS or loopback HTTP"):
                self._load(directory, {
                    "workspace": directory,
                    "ollama_base_url": "http://inference.example.com:11434",
                })

    def test_rejects_mcp_server_without_explicit_host_allowlist(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "allowed_hosts"):
                self._load(directory, {
                    "workspace": directory,
                    "mcp_servers": [{
                        "name": "calendar",
                        "endpoint": "https://mcp.example.com",
                        "allowed_hosts": [],
                        "allowed_tools": ["list_events"],
                    }],
                })

    def test_rejects_mcp_server_with_non_https_endpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HTTPS URL"):
                self._load(directory, {
                    "workspace": directory,
                    "mcp_servers": [{
                        "name": "calendar",
                        "endpoint": "http://mcp.example.com",
                        "allowed_hosts": ["mcp.example.com"],
                        "allowed_tools": ["list_events"],
                    }],
                })

    def test_rejects_remote_postgres_without_tls(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "remote databases require TLS"):
                self._load(directory, {
                    "workspace": directory,
                    "storage_backend": "postgres",
                    "database_url": "postgresql://yuki:secret@db.example.com/yuki",
                })

    def test_accepts_remote_postgres_with_verified_tls_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = self._load(directory, {
                "workspace": directory,
                "storage_backend": "postgres",
                "database_url": "postgresql://yuki:secret@db.example.com/yuki?sslmode=verify-full",
            })
            self.assertEqual(config["storage_backend"], "postgres")
            self.assertEqual(config["database_url"].split("@", 1)[1],
                             "db.example.com/yuki?sslmode=verify-full")

    def test_conversation_memory_can_be_enabled_by_environment_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"YUKI_CONVERSATION_MEMORY_ENABLED": "true"}, clear=True
        ):
            config = self._load(directory, {"workspace": directory})
            self.assertTrue(config["conversation_memory_enabled"])


if __name__ == "__main__":
    unittest.main()
