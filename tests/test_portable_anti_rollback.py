import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from yuki_trust.portable_bootstrap import BootstrapError
from yuki_trust.anti_rollback import advance_release_state


class AntiRollbackStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "release-state.json"

    def test_first_release_initializes_state(self):
        result = advance_release_state(self.state, 10, b"signed manifest 10")
        self.assertEqual(result["highest_sequence"], 10)
        stored = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(stored["highest_sequence"], 10)
        self.assertEqual(stored["manifest_sha256"], result["manifest_sha256"])

    def test_newer_release_advances_monotonic_state(self):
        advance_release_state(self.state, 10, b"manifest 10")
        result = advance_release_state(self.state, 11, b"manifest 11")
        self.assertEqual(result["highest_sequence"], 11)

    def test_older_release_is_rejected(self):
        advance_release_state(self.state, 11, b"manifest 11")
        with self.assertRaisesRegex(BootstrapError, "rollback rejected"):
            advance_release_state(self.state, 10, b"manifest 10")
        self.assertEqual(json.loads(self.state.read_text())["highest_sequence"], 11)

    def test_same_sequence_and_same_manifest_is_idempotent(self):
        first = advance_release_state(self.state, 11, b"manifest 11")
        second = advance_release_state(self.state, 11, b"manifest 11")
        self.assertEqual(first, second)

    def test_same_sequence_with_different_manifest_is_rejected(self):
        advance_release_state(self.state, 11, b"manifest A")
        with self.assertRaisesRegex(BootstrapError, "equivocation"):
            advance_release_state(self.state, 11, b"manifest B")

    def _write_existing_state(self, payload):
        self.state.write_text(payload, encoding="utf-8")
        if os.name != "nt":
            self.state.chmod(0o600)

    def test_corrupt_state_fails_closed(self):
        self._write_existing_state("{broken")
        with self.assertRaisesRegex(BootstrapError, "corrupt"):
            advance_release_state(self.state, 12, b"manifest 12")

    def test_unknown_state_schema_fails_closed(self):
        self._write_existing_state(json.dumps({"schema_version": 99}))
        with self.assertRaisesRegex(BootstrapError, "invalid schema"):
            advance_release_state(self.state, 12, b"manifest 12")

    def test_rejects_symlink_state_file(self):
        target = self.root / "target"
        target.write_text("{}", encoding="utf-8")
        try:
            self.state.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(BootstrapError, "must not be a symlink"):
            advance_release_state(self.state, 1, b"manifest")

    def test_rejects_relative_state_path(self):
        with self.assertRaisesRegex(BootstrapError, "existing host-owned directory"):
            advance_release_state("release-state.json", 1, b"manifest")

    def test_rejects_invalid_sequence(self):
        for value in (True, -1, "12"):
            with self.subTest(value=value):
                with self.assertRaises(BootstrapError):
                    advance_release_state(self.state, value, b"manifest")


    def test_concurrent_launch_attempts_never_lower_highest_sequence(self):
        sequences = list(range(20, 30))
        def attempt(sequence):
            try:
                return advance_release_state(self.state, sequence, f'manifest-{sequence}'.encode())
            except BootstrapError as exc:
                if 'rollback rejected' in str(exc):
                    return None
                raise
        with ThreadPoolExecutor(max_workers=len(sequences)) as pool:
            list(pool.map(attempt, sequences))
        stored = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(stored['highest_sequence'], max(sequences))

if __name__ == "__main__":
    unittest.main()
