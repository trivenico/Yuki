from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import time
import unittest

from core.maintenance_fence import (
    MaintenanceBusyError,
    MaintenanceFence,
    MaintenanceFenceError,
)


def _acquire_after_signal(profile_root: str, start, acquired) -> None:
    start.wait(5)
    with MaintenanceFence(profile_root, timeout_seconds=2).lease():
        acquired.set()


def _hold_fence(profile_root: str, ready, release) -> None:
    fence = MaintenanceFence(profile_root, timeout_seconds=5)
    with fence.lease():
        ready.set()
        release.wait(5)


@unittest.skipUnless(os.name == "posix", "maintenance-fence profile validation is POSIX-only until Windows ACL verification exists")
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

    def test_forked_child_does_not_inherit_parent_lock_descriptor(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("fork-based process test is unavailable on this platform")
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = str(Path(temp) / "profile")
            context = multiprocessing.get_context("fork")
            start, acquired = context.Event(), context.Event()
            with MaintenanceFence(root, timeout_seconds=2).lease():
                child = context.Process(target=_acquire_after_signal, args=(root, start, acquired))
                child.start()
            start.set()
            try:
                self.assertTrue(acquired.wait(4), "child should acquire after parent releases its lease")
            finally:
                child.join(5)
                if child.is_alive():
                    child.terminate()
                    child.join(5)
            self.assertEqual(child.exitcode, 0)

    def test_timeout_validation_rejects_non_finite_and_out_of_range_values(self):
        import tempfile
        invalid = (True, -1, 3601, float("nan"), float("inf"), float("-inf"), 10 ** 10000)
        with tempfile.TemporaryDirectory() as temp:
            for index, value in enumerate(invalid):
                with self.subTest(index=index, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        MaintenanceFence(Path(temp) / "profile", timeout_seconds=value)
            fence = MaintenanceFence(Path(temp) / "profile", timeout_seconds=0)
            for index, value in enumerate(invalid):
                with self.subTest(lease_index=index, lease_value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        with fence.lease(timeout_seconds=value):
                            self.fail("invalid timeout must be rejected before acquiring a lease")

    def test_timeout_validation_accepts_finite_bounded_values(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            for value in (0, 0.25, 10, 3600):
                with self.subTest(value=value):
                    fence = MaintenanceFence(Path(temp) / f"profile-{value}", timeout_seconds=value)
                    self.assertEqual(fence.timeout_seconds, float(value))

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


@unittest.skipUnless(os.name == "nt", "Windows-specific fail-closed boundary")
class WindowsMaintenanceFenceBoundaryTests(unittest.TestCase):
    def test_maintenance_fence_fails_closed_without_creating_profile_or_lock(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private-profile"
            fence = MaintenanceFence(root)
            with self.assertRaises(MaintenanceFenceError):
                with fence.lease():
                    self.fail("Windows profile ACL validation must fail closed")
            self.assertFalse(root.exists())
            self.assertFalse((root / "maintenance.lock").exists())


if __name__ == "__main__":
    unittest.main()
