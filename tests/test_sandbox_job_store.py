import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from automation.sandbox_job_store import SandboxJobStore, SandboxJobStoreError


class SandboxJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.root.mkdir(mode=0o700)
        os.chmod(self.root, 0o700)
        self.store = SandboxJobStore(self.root / "jobs.sqlite3")
        self.job_id = "job_" + "a" * 32

    def test_database_is_created_with_private_permissions(self):
        self.assertEqual(stat.S_IMODE((self.root / "jobs.sqlite3").stat().st_mode), 0o600)

    def test_create_and_reopen_preserves_job_metadata(self):
        record = self.store.create(
            job_id=self.job_id,
            staging_directory="/srv/yuki/jobs/" + self.job_id,
            container_name="yuki-job-" + "a" * 32,
        )
        reopened = SandboxJobStore(self.root / "jobs.sqlite3")
        self.assertEqual(reopened.get(self.job_id), record)
        self.assertEqual(reopened.list_nonterminal(), (record,))

    def test_compare_and_swap_transition_persists_manifest(self):
        self.store.create(job_id=self.job_id, staging_directory="/srv/yuki/jobs/" + self.job_id)
        digest = "b" * 64
        record = self.store.transition(
            self.job_id,
            expected_states=("created",),
            new_state="launching",
            container_name="yuki-job-" + "a" * 32,
        )
        self.assertEqual(record.state, "launching")
        record = self.store.transition(
            self.job_id, expected_states=("launching",), new_state="running"
        )
        self.assertEqual(record.state, "running")
        record = self.store.transition(
            self.job_id, expected_states=("running",), new_state="frozen"
        )
        self.assertEqual(record.state, "frozen")
        record = self.store.transition(
            self.job_id,
            expected_states=("frozen",),
            new_state="validated",
            manifest_digest=digest,
            promoted_paths=("report.txt",),
        )
        self.assertEqual(record.manifest_digest, digest)
        self.assertEqual(record.promoted_paths, ("report.txt",))

    def test_stale_state_transition_is_rejected(self):
        self.store.create(job_id=self.job_id, staging_directory="/srv/yuki/jobs/" + self.job_id)
        self.store.transition(self.job_id, expected_states=("created",), new_state="launching")
        with self.assertRaises(SandboxJobStoreError):
            self.store.transition(self.job_id, expected_states=("created",), new_state="frozen")

    def test_migrates_legacy_schema_without_losing_jobs(self):
        database = self.root / "legacy.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("""
            CREATE TABLE sandbox_jobs (
                job_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK(state IN (
                    'created','running','frozen','validated','promoted',
                    'failed','cancelled','cleaned'
                )),
                staging_directory TEXT NOT NULL,
                container_name TEXT,
                manifest_digest TEXT,
                promoted_paths_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        connection.execute(
            "INSERT INTO sandbox_jobs VALUES(?,?,?,?,?,?,?,?)",
            (self.job_id, "created", "/srv/yuki/jobs/" + self.job_id,
             None, None, "[]", 1.0, 1.0),
        )
        connection.commit()
        connection.close()

        migrated = SandboxJobStore(database)
        record = migrated.get(self.job_id)
        self.assertEqual(record.state, "created")
        launched = migrated.transition(
            self.job_id, expected_states=("created",), new_state="launching",
            container_name="yuki-job-" + "a" * 32,
        )
        self.assertEqual(launched.state, "launching")

    def test_rejects_symlink_database_and_permissive_parent(self):
        other = self.root / "actual.sqlite3"
        other.touch()
        link = self.root / "link.sqlite3"
        link.symlink_to(other)
        with self.assertRaises(ValueError):
            SandboxJobStore(link)
        permissive = Path(self.temp.name) / "permissive"
        permissive.mkdir(mode=0o755)
        os.chmod(permissive, 0o755)
        with self.assertRaises(ValueError):
            SandboxJobStore(permissive / "jobs.sqlite3")

    def test_rejects_untrusted_container_names_and_manifest_digests(self):
        self.store.create(job_id=self.job_id, staging_directory="/srv/yuki/jobs/" + self.job_id)
        with self.assertRaises(SandboxJobStoreError):
            self.store.transition(
                self.job_id, expected_states=("created",), new_state="launching",
                container_name="arbitrary-container",
            )
        with self.assertRaises(SandboxJobStoreError):
            self.store.transition(
                self.job_id, expected_states=("created",), new_state="launching",
                manifest_digest="not-a-digest",
            )



    def test_rejects_transition_outside_state_machine(self):
        self.store.create(job_id=self.job_id, staging_directory="/srv/yuki/jobs/" + self.job_id)
        with self.assertRaises(SandboxJobStoreError):
            self.store.transition(
                self.job_id, expected_states=("created",), new_state="promoted"
            )
        with self.assertRaises(SandboxJobStoreError):
            self.store.transition(
                self.job_id, expected_states=("created",), new_state="running"
            )
        self.assertEqual(self.store.get(self.job_id).state, "created")

if __name__ == "__main__":
    unittest.main()
