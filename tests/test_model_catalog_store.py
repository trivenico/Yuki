import tempfile
import unittest
from pathlib import Path

from providers.model_catalog import new_catalog
from providers.model_catalog_store import ModelCatalogStore, ModelCatalogStoreError


class ModelCatalogStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.sqlite3"
        self.store = ModelCatalogStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_initial_state_and_round_trip(self):
        self.assertEqual(self.store.load(), {"revision": 0, "catalog": new_catalog()})
        catalog = new_catalog()
        catalog["models"].append({"model_id": "yuki/core", "model_version": "0.1"})
        saved = self.store.save(catalog, expected_revision=0, actor="owner-1",
                                action="catalog.register")
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.store.load()["catalog"], catalog)
        self.assertTrue(self.store.verify_audit_chain())

    def test_stale_writer_cannot_overwrite_new_revision(self):
        self.store.save(new_catalog(), expected_revision=0, actor="owner-1",
                        action="catalog.update")
        with self.assertRaisesRegex(ModelCatalogStoreError, "revision_conflict"):
            self.store.save(new_catalog(), expected_revision=0, actor="owner-2",
                            action="catalog.update")
        self.assertEqual(self.store.load()["revision"], 1)

    def test_audit_chain_verifies_across_multiple_revisions(self):
        for revision in range(4):
            self.store.save(new_catalog(), expected_revision=revision,
                            actor="reviewer-1", action="catalog.review")
        events = self.store.audit_events()
        self.assertEqual([event["revision"] for event in events], [1, 2, 3, 4])
        self.assertEqual(events[1]["previous_event_sha256"], events[0]["event_sha256"])
        self.assertTrue(self.store.verify_audit_chain())

    def test_reopen_preserves_state_and_audit(self):
        self.store.save(new_catalog(), expected_revision=0, actor="owner-1",
                        action="catalog.update")
        reopened = ModelCatalogStore(self.path)
        self.assertEqual(reopened.load()["revision"], 1)
        self.assertTrue(reopened.verify_audit_chain())

    def test_rejects_bad_actor_action_and_catalog(self):
        with self.assertRaisesRegex(ModelCatalogStoreError, "invalid_audit_actor"):
            self.store.save(new_catalog(), expected_revision=0, actor="../bad",
                            action="catalog.update")
        with self.assertRaisesRegex(ModelCatalogStoreError, "invalid_audit_action"):
            self.store.save(new_catalog(), expected_revision=0, actor="owner-1",
                            action="INVALID")
        with self.assertRaisesRegex(ModelCatalogStoreError, "invalid_catalog"):
            self.store.save({"schema_version": 1, "models": [object()]},
                            expected_revision=0, actor="owner-1",
                            action="catalog.update")

    def test_rejects_symlink_database_path(self):
        link = Path(self.temp.name) / "catalog-link.sqlite3"
        try:
            link.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ModelCatalogStoreError, "symlink"):
            ModelCatalogStore(link)


if __name__ == "__main__":
    unittest.main()
