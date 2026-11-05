"""Durable local SQLite storage for immutable dataset intake records.

Registration is append-only and quarantines every dataset until independent
review is represented by an eligible manifest. This is single-host storage.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path

from providers.dataset_artifact_manifest import (
    DatasetManifestError,
    validate_dataset_manifest,
)


class DatasetStoreError(RuntimeError):
    """Dataset persistence, admission, or integrity failure."""


_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


class DatasetStore:
    def __init__(self, path, *, timeout=5.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 30:
            raise ValueError("invalid_timeout")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise DatasetStoreError("database_must_not_be_symlink")
        self.timeout = float(timeout)
        try:
            with self._connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS datasets (
                        dataset_id TEXT NOT NULL,
                        dataset_revision TEXT NOT NULL,
                        manifest_json TEXT NOT NULL,
                        manifest_sha256 TEXT NOT NULL,
                        state TEXT NOT NULL CHECK(state IN ('quarantined','approved','rejected')),
                        PRIMARY KEY(dataset_id,dataset_revision)
                    );
                    CREATE TABLE IF NOT EXISTS dataset_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        dataset_id TEXT NOT NULL,
                        dataset_revision TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        manifest_sha256 TEXT NOT NULL,
                        previous_event_sha256 TEXT NOT NULL,
                        event_sha256 TEXT NOT NULL UNIQUE
                    );
                """)
        except sqlite3.Error as exc:
            raise DatasetStoreError("initialization_failed") from exc

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=self.timeout, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=%d" % int(self.timeout * 1000))
        return db

    @staticmethod
    def _event_hash(sequence, dataset_id, revision, actor, action, manifest_hash, previous):
        payload = [sequence, dataset_id, revision, actor, action, manifest_hash, previous]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    def register(self, manifest, *, actor):
        """Persist a new immutable revision in quarantine; duplicate revisions fail."""
        if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
            raise DatasetStoreError("invalid_audit_actor")
        try:
            normalized = validate_dataset_manifest(manifest)
        except DatasetManifestError as exc:
            raise DatasetStoreError(str(exc)) from exc
        serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        dataset_id, revision = normalized["dataset_id"], normalized["dataset_revision"]
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            state = "approved" if normalized["training_eligible"] else "quarantined"
            db.execute(
                "INSERT INTO datasets VALUES(?,?,?,?,?)",
                (dataset_id, revision, serialized, digest, state),
            )
            prior = db.execute(
                "SELECT event_sha256 FROM dataset_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous = prior[0] if prior else ""
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM dataset_events").fetchone()[0]
            event = self._event_hash(sequence, dataset_id, revision, actor,
                                     "dataset.register", digest, previous)
            db.execute(
                "INSERT INTO dataset_events VALUES(?,?,?,?,?,?,?,?)",
                (sequence, dataset_id, revision, actor, "dataset.register",
                 digest, previous, event),
            )
            db.execute("COMMIT")
            return {"dataset_id": dataset_id, "dataset_revision": revision,
                    "state": state, "manifest_sha256": digest, "event_sha256": event}
        except sqlite3.IntegrityError as exc:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise DatasetStoreError("dataset_revision_already_exists") from exc
        except DatasetStoreError:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise DatasetStoreError("dataset_write_failed") from exc
        finally:
            db.close()

    def get(self, dataset_id, dataset_revision):
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT manifest_json,manifest_sha256,state FROM datasets WHERE dataset_id=? AND dataset_revision=?",
                    (dataset_id, dataset_revision),
                ).fetchone()
            if row is None:
                raise DatasetStoreError("dataset_not_found")
            manifest = json.loads(row[0])
            digest = hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(digest, row[1]):
                raise DatasetStoreError("dataset_manifest_integrity_failure")
            return {"manifest": manifest, "manifest_sha256": row[1], "state": row[2]}
        except DatasetStoreError:
            raise
        except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise DatasetStoreError("dataset_read_failed") from exc

    def list_datasets(self, *, limit=1000):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("invalid_dataset_limit")
        try:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT dataset_id,dataset_revision,state,manifest_sha256 FROM datasets ORDER BY dataset_id,dataset_revision LIMIT ?",
                    (limit,),
                ).fetchall()
            return [{"dataset_id": row[0], "dataset_revision": row[1],
                     "state": row[2], "manifest_sha256": row[3]} for row in rows]
        except sqlite3.Error as exc:
            raise DatasetStoreError("dataset_list_failed") from exc

    def verify_audit_chain(self):
        try:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT sequence,dataset_id,dataset_revision,actor,action,manifest_sha256,previous_event_sha256,event_sha256 FROM dataset_events ORDER BY sequence"
                ).fetchall()
            previous = ""
            for expected, row in enumerate(rows, 1):
                sequence, dataset_id, revision, actor, action, manifest_hash, prior, digest = row
                if sequence != expected or prior != previous:
                    return False
                calculated = self._event_hash(sequence, dataset_id, revision, actor,
                                               action, manifest_hash, previous)
                if not hmac.compare_digest(calculated, digest):
                    return False
                stored = self.get(dataset_id, revision)
                if not hmac.compare_digest(stored["manifest_sha256"], manifest_hash):
                    return False
                previous = digest
            return True
        except (sqlite3.Error, DatasetStoreError):
            return False
