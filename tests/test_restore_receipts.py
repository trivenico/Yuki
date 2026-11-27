import json
import os
import tempfile
import unittest
from pathlib import Path

from ui.desktop.restore_receipts import (
    RestoreReceiptError,
    create_restore_receipt,
    inspect_restore_receipt,
    update_restore_receipt,
)


@unittest.skipUnless(os.name == "posix", "durable directory fsync is POSIX-only")
class RestoreReceiptTests(unittest.TestCase):
    def test_prepared_receipt_is_inspectable_without_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=3, size_bytes=42)
            observed = inspect_restore_receipt(
                Path(result["receipt_path"]), expected_parent=root,
                operation_id=result["operation_id"],
            )
            self.assertEqual(observed["state"], "PREPARED")
            self.assertEqual(observed["observed_outcome"], "NOT_CURRENTLY_PUBLISHED")
            self.assertFalse(observed["destination_exists"])

    def test_prepared_receipt_with_destination_is_unconfirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=1, size_bytes=1)
            (root / "restored").mkdir()
            observed = inspect_restore_receipt(
                Path(result["receipt_path"]), expected_parent=root,
                operation_id=result["operation_id"],
            )
            self.assertEqual(observed["observed_outcome"], "PUBLISHED_UNCONFIRMED")

    def test_terminal_receipt_state_is_durable_and_inspectable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            (root / "restored" / ".yuki").mkdir(parents=True)
            update_restore_receipt(Path(result["receipt_path"]), result["operation_id"], "COMMITTED")
            observed = inspect_restore_receipt(
                Path(result["receipt_path"]), expected_parent=root,
                operation_id=result["operation_id"],
            )
            self.assertEqual(observed["observed_outcome"], "COMMITTED")
            with self.assertRaises(RestoreReceiptError):
                update_restore_receipt(Path(result["receipt_path"]), result["operation_id"], "NOT_PUBLISHED")

    def test_receipt_rejects_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            original = Path(result["receipt_path"])
            linked = root / "linked.json"
            try:
                linked.symlink_to(original)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(RestoreReceiptError):
                inspect_restore_receipt(linked, expected_parent=root, operation_id=result["operation_id"])
            hardlink = root / "hardlink.json"
            os.link(original, hardlink)
            with self.assertRaises(RestoreReceiptError):
                inspect_restore_receipt(original, expected_parent=root, operation_id=result["operation_id"])

    def test_receipt_rejects_malformed_and_wrong_schema_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            path = Path(result["receipt_path"])
            data = json.loads(path.read_text())
            data["file_count"] = "not-an-integer"
            path.write_text(json.dumps(data))
            path.chmod(0o600)
            with self.assertRaises(RestoreReceiptError):
                inspect_restore_receipt(path, expected_parent=root, operation_id=result["operation_id"])

    def test_receipt_rejects_non_finite_and_boolean_timestamps(self):
        for invalid_timestamp in (float("nan"), float("inf"), True):
            with self.subTest(timestamp=invalid_timestamp):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
                    path = Path(result["receipt_path"])
                    data = json.loads(path.read_text())
                    data["created_at"] = invalid_timestamp
                    path.write_text(json.dumps(data))
                    path.chmod(0o600)
                    with self.assertRaises(RestoreReceiptError):
                        inspect_restore_receipt(
                            path, expected_parent=root, operation_id=result["operation_id"],
                        )

    def test_update_rejects_unhashable_state_without_type_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            with self.assertRaises(RestoreReceiptError):
                update_restore_receipt(
                    Path(result["receipt_path"]), result["operation_id"], [],
                )

    def test_operation_id_must_match_receipt_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            with self.assertRaises(RestoreReceiptError):
                inspect_restore_receipt(
                    Path(result["receipt_path"]), expected_parent=root,
                    operation_id="0" * 32,
                )

    def test_committed_receipt_with_missing_destination_requires_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            update_restore_receipt(Path(result["receipt_path"]), result["operation_id"], "COMMITTED")
            observed = inspect_restore_receipt(
                Path(result["receipt_path"]), expected_parent=root,
                operation_id=result["operation_id"],
            )
            self.assertEqual(observed["state"], "COMMITTED")
            self.assertEqual(observed["observed_outcome"], "RECOVERY_REQUIRED")

    def test_not_published_receipt_with_existing_destination_requires_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            update_restore_receipt(Path(result["receipt_path"]), result["operation_id"], "NOT_PUBLISHED")
            (root / "restored").mkdir()
            observed = inspect_restore_receipt(
                Path(result["receipt_path"]), expected_parent=root,
                operation_id=result["operation_id"],
            )
            self.assertEqual(observed["observed_outcome"], "RECOVERY_REQUIRED")

    def test_receipt_creation_rejects_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "restored"
            destination.mkdir()
            with self.assertRaisesRegex(RestoreReceiptError, "must not exist"):
                create_restore_receipt(destination, file_count=0, size_bytes=0)

    def test_receipt_creation_rejects_invalid_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RestoreReceiptError):
                create_restore_receipt(Path(directory) / "restored", file_count=-1, size_bytes=0)
            with self.assertRaises(RestoreReceiptError):
                create_restore_receipt(Path(directory) / "restored", file_count=0, size_bytes=True)


    def test_receipt_rejects_group_or_world_writable_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o777)
            try:
                with self.assertRaisesRegex(RestoreReceiptError, "not group/world writable"):
                    create_restore_receipt(root / "restored", file_count=0, size_bytes=0)
            finally:
                root.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
