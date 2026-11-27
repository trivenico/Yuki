"""Unit tests for Linux isolation read-back policy checks."""
from __future__ import annotations

import unittest

from evaluation.linux_namespace_isolation import (
    LinuxIsolationSnapshot,
    LinuxNamespaceBaseline,
    verify_linux_process_isolation,
)

_NAMES = ("cgroup", "ipc", "mnt", "net", "pid", "user", "uts")


def _baseline():
    return LinuxNamespaceBaseline({name: index + 100 for index, name in enumerate(_NAMES)})


def _snapshot(**changes):
    namespaces = {name: index + 200 for index, name in enumerate(_NAMES)}
    values = {
        "uid": (1001, 1001, 1001, 1001),
        "gid": (1002, 1002, 1002, 1002),
        "effective_capabilities": 0,
        "no_new_privileges": True,
        "namespace_inodes": namespaces,
    }
    values.update(changes)
    return LinuxIsolationSnapshot(**values)


class LinuxNamespaceIsolationPolicyTests(unittest.TestCase):
    def test_accepts_complete_isolated_snapshot(self):
        self.assertTrue(verify_linux_process_isolation(
            _snapshot(), _baseline(), expected_uid=1001, expected_gid=1002
        ))

    def test_rejects_shared_required_namespace(self):
        namespaces = dict(_snapshot().namespace_inodes)
        namespaces["net"] = _baseline().inodes["net"]
        self.assertFalse(verify_linux_process_isolation(
            _snapshot(namespace_inodes=namespaces),
            _baseline(), expected_uid=1001, expected_gid=1002
        ))

    def test_rejects_unexpected_credentials(self):
        self.assertFalse(verify_linux_process_isolation(
            _snapshot(uid=(1001, 1001, 0, 1001)),
            _baseline(), expected_uid=1001, expected_gid=1002
        ))

    def test_rejects_effective_capabilities(self):
        self.assertFalse(verify_linux_process_isolation(
            _snapshot(effective_capabilities=1),
            _baseline(), expected_uid=1001, expected_gid=1002
        ))

    def test_rejects_missing_no_new_privileges(self):
        self.assertFalse(verify_linux_process_isolation(
            _snapshot(no_new_privileges=False),
            _baseline(), expected_uid=1001, expected_gid=1002
        ))

    def test_rejects_incomplete_baseline(self):
        self.assertFalse(verify_linux_process_isolation(
            _snapshot(), LinuxNamespaceBaseline({}), expected_uid=1001, expected_gid=1002
        ))


if __name__ == "__main__":
    unittest.main()
