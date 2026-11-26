import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from core.auth import BuiltInAuthService
from core.conversation_store import ConversationStore
from core.device_pairing import DevicePairingService
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService


class RecordingFence:
    def __init__(self):
        self.acquisitions = 0

    @contextmanager
    def lease(self):
        self.acquisitions += 1
        yield


class SharedMaintenanceFenceStoreTests(unittest.TestCase):
    def test_web_host_sqlite_stores_acquire_the_injected_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fence = RecordingFence()

            BuiltInAuthService(root / "auth.sqlite3", maintenance_fence=fence)
            ConversationStore(root / "conversations.sqlite3", maintenance_fence=fence)
            pairing = DevicePairingService(root / "inference.sqlite3", maintenance_fence=fence)
            InferenceJobService(root / "inference.sqlite3", pairing, maintenance_fence=fence)
            SQLiteIncomeOpportunityStore(
                str(root / "income.sqlite3"), maintenance_fence=fence,
            )

            # Each store takes its fence before opening SQLite for schema setup.
            self.assertEqual(fence.acquisitions, 5)

    def test_invalid_fence_is_rejected_before_database_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.sqlite3"
            with self.assertRaisesRegex(TypeError, "maintenance_fence"):
                BuiltInAuthService(path, maintenance_fence=object())
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
