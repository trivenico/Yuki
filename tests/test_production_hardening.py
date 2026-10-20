"""Production-hardening regression tests; no network access is required."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.mcp_client import MCPError, MCPHTTPClient
import yuki_learning


class MCPPolicyTests(unittest.TestCase):
    def test_rejects_non_https_and_credentials(self):
        with self.assertRaises(MCPError):
            MCPHTTPClient("http://example.com/mcp", allowed_hosts=["example.com"])
        with self.assertRaises(MCPError):
            MCPHTTPClient("https://user:pass@example.com/mcp", allowed_hosts=["example.com"])

    def test_requires_exact_allowlisted_hostname(self):
        with patch("core.mcp_client.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]):
            with self.assertRaises(MCPError):
                MCPHTTPClient("https://sub.example.com/mcp", allowed_hosts=["example.com"])

    def test_blocks_mixed_public_private_dns_answers(self):
        with patch("core.mcp_client.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]):
            with self.assertRaises(MCPError):
                MCPHTTPClient("https://example.com/mcp", allowed_hosts=["example.com"])

    def test_approval_must_be_literal_true(self):
        with patch("core.mcp_client.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]):
            for decision in (None, 1, "yes", False):
                client = MCPHTTPClient("https://example.com/mcp",
                                       allowed_hosts=["example.com"],
                                       allowed_tools=["read_file"],
                                       approver=lambda _name, _args, value=decision: value)
                with self.subTest(decision=decision), self.assertRaises(MCPError):
                    client.call_tool("read_file", {})
            async def async_approval(_name, _args):
                return True
            client = MCPHTTPClient("https://example.com/mcp",
                                   allowed_hosts=["example.com"],
                                   allowed_tools=["read_file"],
                                   approver=async_approval)
            with self.assertRaises(MCPError):
                client.call_tool("read_file", {})


class LearningStoreTests(unittest.TestCase):
    def test_atomic_store_redacts_secrets_and_restricts_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "private" / "history.json"
            with patch.object(yuki_learning, "HISTORY_FILE", target):
                self.assertTrue(yuki_learning.save_history([{
                    "task": "debug", "outcome": "ok",
                    "approach": "api_key=supersecret", "lesson": "rotate keys",
                    "created_at": "2026-01-01",
                }]))
                loaded = yuki_learning.load_history()
                self.assertIn("[REDACTED]", loaded[0]["approach"])
                self.assertNotIn("supersecret", target.read_text(encoding="utf-8"))
                if os.name == "posix":
                    self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(list(target.parent.glob(".yuki-learning-*.tmp")), [])

    def test_invalid_root_does_not_raise_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "history.json"
            with patch.object(yuki_learning, "HISTORY_FILE", target):
                self.assertFalse(yuki_learning.save_history({"not": "a list"}))
                self.assertEqual(yuki_learning.load_history(), [])


if __name__ == "__main__":
    unittest.main()
