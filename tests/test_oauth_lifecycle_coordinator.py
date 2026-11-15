import multiprocessing
import tempfile
import unittest
from pathlib import Path

from core.oauth_lifecycle_coordinator import (
    OAuthLifecycleCoordinationError,
    SQLiteOAuthLifecycleCoordinator,
)


def _hold_lifecycle_lock(path, ready, release):
    coordinator = SQLiteOAuthLifecycleCoordinator(path, busy_timeout_ms=2000)
    with coordinator.coordinate(
        owner_id="owner-1", provider_id="provider-1", account_id="account-1",
    ):
        ready.set()
        release.wait(5)


class OAuthLifecycleCoordinatorTests(unittest.TestCase):
    def test_lock_is_shared_across_processes_and_released_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "lifecycle.sqlite")
            coordinator = SQLiteOAuthLifecycleCoordinator(path, busy_timeout_ms=100)
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            process = context.Process(
                target=_hold_lifecycle_lock, args=(path, ready, release),
            )
            process.start()
            try:
                self.assertTrue(ready.wait(5), "child did not acquire lifecycle lock")
                with self.assertRaisesRegex(
                    OAuthLifecycleCoordinationError, "coordinator_lock_unavailable",
                ):
                    with coordinator.coordinate(
                        owner_id="owner-2", provider_id="provider-1",
                        account_id="account-1",
                    ):
                        self.fail("a second process entered the critical section")
            finally:
                release.set()
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            self.assertEqual(process.exitcode, 0)
            with coordinator.coordinate(
                owner_id="owner-1", provider_id="provider-1", account_id="account-1",
            ):
                pass

    def test_rejects_invalid_identity_and_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "lifecycle.sqlite")
            with self.assertRaises(ValueError):
                SQLiteOAuthLifecycleCoordinator(path, busy_timeout_ms=0)
            coordinator = SQLiteOAuthLifecycleCoordinator(path)
            with self.assertRaises(ValueError):
                with coordinator.coordinate(
                    owner_id="../owner", provider_id="provider-1",
                ):
                    pass


if __name__ == "__main__":
    unittest.main()
