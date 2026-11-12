import tempfile
import unittest
from pathlib import Path

from core.annotation_assets import AnnotationAssetError, SQLiteAnnotationAssetStore


class AnnotationAssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = SQLiteAnnotationAssetStore(
            str(root / "assets"), str(root / "assets.sqlite3"), max_bytes=1024
        )
        self.owner = "owner:alice"
        self.project = "a" * 32

    def tearDown(self):
        self.temp.cleanup()

    def test_upload_is_quarantined_until_explicit_clean_scan(self):
        data = b"\x89PNG\r\n\x1a\n" + b"image-data"
        asset = self.store.upload(self.owner, self.project, data)
        self.assertEqual(asset.scan_status, "quarantined")
        with self.assertRaisesRegex(LookupError, "not_found"):
            self.store.read(self.owner, self.project, asset.asset_id)
        self.assertTrue(self.store.mark_scan_result(
            self.owner, self.project, asset.asset_id, clean=True
        ))
        self.assertEqual(self.store.read(self.owner, self.project, asset.asset_id), data)
        self.assertFalse(self.store.mark_scan_result(
            self.owner, self.project, asset.asset_id, clean=False
        ))

    def test_owner_and_project_are_isolated(self):
        asset = self.store.upload(self.owner, self.project, b"\xff\xd8\xffjpeg")
        self.store.mark_scan_result(self.owner, self.project, asset.asset_id, clean=True)
        with self.assertRaisesRegex(LookupError, "not_found"):
            self.store.read("owner:bob", self.project, asset.asset_id)
        with self.assertRaisesRegex(LookupError, "not_found"):
            self.store.read(self.owner, "b" * 32, asset.asset_id)

    def test_rejects_remote_paths_unknown_formats_and_oversized_assets(self):
        with self.assertRaisesRegex(AnnotationAssetError, "unsupported"):
            self.store.upload(self.owner, self.project, b"https://example.com/image.png")
        with self.assertRaisesRegex(AnnotationAssetError, "oversized"):
            self.store.upload(self.owner, self.project, b"x" * 1025)

    def test_rejects_symlinked_storage_root(self):
        root = Path(self.temp.name)
        target = root / "target"
        target.mkdir()
        link = root / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaisesRegex(ValueError, "must_not_be_symlinks"):
            SQLiteAnnotationAssetStore(str(link), str(root / "other.sqlite3"))

    def test_integrity_is_checked_on_read(self):
        asset = self.store.upload(self.owner, self.project, b"GIF89aimage")
        self.store.mark_scan_result(self.owner, self.project, asset.asset_id, clean=True)
        path = Path(self.temp.name) / "assets" / __import__("hashlib").sha256(
            self.owner.encode()
        ).hexdigest() / asset.asset_id
        path.write_bytes(b"GIF89aother")
        with self.assertRaisesRegex(AnnotationAssetError, "integrity"):
            self.store.read(self.owner, self.project, asset.asset_id)

    def test_delete_revokes_access_and_removes_bytes(self):
        asset = self.store.upload(self.owner, self.project, b"ID3audio")
        self.assertTrue(self.store.delete(self.owner, self.project, asset.asset_id))
        self.assertFalse(self.store.delete(self.owner, self.project, asset.asset_id))
        with self.assertRaisesRegex(LookupError, "not_found"):
            self.store.read(self.owner, self.project, asset.asset_id)


if __name__ == "__main__":
    unittest.main()
