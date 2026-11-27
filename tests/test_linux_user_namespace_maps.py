"""Tests for fail-closed Linux user-namespace mapping input validation."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from evaluation.linux_user_namespace_maps import (
    LinuxIdMapRange,
    install_linux_user_namespace_maps,
)


class _PinnedProcess:
    pid = 4321

    def verify(self):
        return True


class LinuxIdMapRangeTests(unittest.TestCase):
    def test_renders_single_id_mapping(self):
        self.assertEqual(LinuxIdMapRange(0, 1001).render(), "0 1001 1\n")

    def test_rejects_boolean_and_overflow(self):
        for mapping in (
            LinuxIdMapRange(True, 1001),
            LinuxIdMapRange(0, 1001, 0),
            LinuxIdMapRange(2**31, 0, 1),
            LinuxIdMapRange(0, 2**31, 1),
        ):
            with self.subTest(mapping=mapping):
                self.assertFalse(mapping.verify())
                with self.assertRaises(ValueError):
                    mapping.render()


class LinuxUserNamespaceMapGateTests(unittest.TestCase):
    def test_rejects_invalid_mapping_before_opening_procfs(self):
        with patch("evaluation.linux_user_namespace_maps.os.open") as open_mock:
            with self.assertRaises(ValueError):
                install_linux_user_namespace_maps(
                    _PinnedProcess(),
                    uid_map=LinuxIdMapRange(0, 1001, 0),
                    gid_map=LinuxIdMapRange(0, 1002),
                )
            open_mock.assert_not_called()

    def test_requires_setgroups_denial(self):
        with patch("evaluation.linux_user_namespace_maps.os.open") as open_mock:
            with self.assertRaises(ValueError):
                install_linux_user_namespace_maps(
                    _PinnedProcess(),
                    uid_map=LinuxIdMapRange(0, 1001),
                    gid_map=LinuxIdMapRange(0, 1002),
                    deny_setgroups=False,
                )
            open_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
