from __future__ import annotations

import multiprocessing
from pathlib import Path
import time
import unittest

from core.maintenance_fence import (
    MaintenanceBusyError,
    MaintenanceFence,
    MaintenanceFenceError,
)


def _hold_fence(profile_root: str, ready, release) -> None:
    fence = MaintenanceFence(profile_root, timeout_seconds=5)
    with fence.lease():
        ready.set()
        release.wait(5)


class MaintenanceFenceTests(unittest.TestCase):
    def test_lease_creates_owner_private_lock_file(self):
        with self.subTest("private profile"):
            import tempfile
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "profile"
                fence = MaintenanceFence(root)
                with fence.lease():
                    lock_path = root / "maintenance.lock"
                    self.assertTrue(lock_path.is_file())
                    self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)
                self.assertTrue(lock_path.exists())

    def test_nested_lease_is_reentrant_in_same_thread(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            fence = MaintenanceFence(Path(temp) / "profile")
            with fence.lease():
                with fence.lease():
                    self.assertTrue((Path(temp) / "profile" / "maintenance.lock").exists())

    def test_exclusive_lock_blocks_independent_process(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("fork-based process test is unavailable on this platform")
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = str(Path(temp) / "profile")
            context = multiprocessing.get_context("fork")
            ready, release = context.Event(), context.Event()
            process = context.Process(target=_hold_fence, args=(root, ready, release))
            process.start()
            try:
                self.assertTrue(ready.wait(5))
                contender = MaintenanceFence(root, timeout_seconds=0.1)
                with self.assertRaises(MaintenanceBusyError):
                    with contender.lease():
                        self.fail("second process must not enter while first owns the lease")
            finally:
                release.set()
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            self.assertEqual(process.exitcode, 0)

    def test_symlinked_profile_root_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            actual = base / "actual"
            actual.mkdir(mode=0o700)
            alias = base / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            fence = MaintenanceFence(alias)
            with self.assertRaises(MaintenanceFenceError):
                with fence.lease():
                    self.fail("symlinked profile root must not be accepted")


if __name__ == "__main__":
    unittest.main()
