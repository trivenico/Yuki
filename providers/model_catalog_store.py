"""Durable local SQLite storage for the Yuki model catalog.

Uses optimistic revisions and a hash-chained audit log. This is single-host
coordination, not distributed consensus or an authorization system.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from providers.model_catalog import list_models, new_catalog


class ModelCatalogStoreError(RuntimeError):
    """Catalog persistence or integrity failure."""


_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ModelCatalogStore:
    def __init__(self, path, *, timeout=5.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 30:
            raise ValueError("invalid_timeout")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ModelCatalogStoreError("database_must_not_be_symlink")
        self.timeout = float(timeout)
        try:
            with self._connection() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS catalog_state (
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        revision INTEGER NOT NULL CHECK(revision>=0),
                        catalog_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS catalog_events (
                        revision INTEGER PRIMARY KEY,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        catalog_sha256 TEXT NOT NULL,
                        previous_event_sha256 TEXT NOT NULL,
                        event_sha256 TEXT NOT NULL UNIQUE
                    );
                """)
                db.execute(
                    "INSERT OR IGNORE INTO catalog_state VALUES(1,0,?)",
                    (self._serialize(new_catalog()),),
                )
        except sqlite3.Error as exc:
            raise ModelCatalogStoreError("initialization_failed") from exc

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=self.timeout, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=%d" % int(self.timeout * 1000))
        return db

    @contextmanager
    def _connection(self):
        """Commit/rollback through the SQLite context manager and always close."""
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _serialize(catalog):
        try:
            list_models(catalog)
            return json.dumps(catalog, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ModelCatalogStoreError("invalid_catalog") from exc

    @staticmethod
    def _event_hash(revision, actor, action, catalog_hash, previous_hash):
        event = [revision, actor, action, catalog_hash, previous_hash]
        return hashlib.sha256(json.dumps(event, separators=(",", ":")).encode()).hexdigest()

    def load(self):
        try:
            with self._connection() as db:
                row = db.execute(
                    "SELECT revision,catalog_json FROM catalog_state WHERE singleton=1"
                ).fetchone()
            if row is None:
                raise ModelCatalogStoreError("catalog_state_missing")
            catalog = json.loads(row[1])
            self._serialize(catalog)
            return {"revision": row[0], "catalog": catalog}
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ModelCatalogStoreError("catalog_read_failed") from exc

    def save(self, catalog, *, expected_revision, actor, action):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ModelCatalogStoreError("invalid_expected_revision")
        if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
            raise ModelCatalogStoreError("invalid_audit_actor")
        if not isinstance(action, str) or not _ACTION.fullmatch(action):
            raise ModelCatalogStoreError("invalid_audit_action")
        serialized = self._serialize(catalog)
        catalog_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM catalog_state WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise ModelCatalogStoreError("catalog_state_missing")
            if row[0] != expected_revision:
                raise ModelCatalogStoreError("catalog_revision_conflict")
            prior = db.execute(
                "SELECT event_sha256 FROM catalog_events ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            previous_hash = prior[0] if prior else ""
            revision = expected_revision + 1
            digest = self._event_hash(
                revision, actor, action, catalog_hash, previous_hash
            )
            db.execute(
                "UPDATE catalog_state SET revision=?,catalog_json=? WHERE singleton=1",
                (revision, serialized),
            )
            db.execute(
                "INSERT INTO catalog_events VALUES(?,?,?,?,?,?)",
                (revision, actor, action, catalog_hash, previous_hash, digest),
            )
            db.execute("COMMIT")
            return {"revision": revision, "catalog_sha256": catalog_hash,
                    "event_sha256": digest}
        except ModelCatalogStoreError:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise ModelCatalogStoreError("catalog_write_failed") from exc
        finally:
            db.close()

    def audit_events(self, *, limit=1000):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("invalid_audit_limit")
        try:
            with self._connection() as db:
                rows = db.execute(
                    """SELECT revision,actor,action,catalog_sha256,
                    previous_event_sha256,event_sha256
                    FROM catalog_events ORDER BY revision LIMIT ?""", (limit,)
                ).fetchall()
            keys = ("revision", "actor", "action", "catalog_sha256",
                    "previous_event_sha256", "event_sha256")
            return [dict(zip(keys, row)) for row in rows]
        except sqlite3.Error as exc:
            raise ModelCatalogStoreError("catalog_audit_read_failed") from exc

    def verify_audit_chain(self):
        events = self.audit_events(limit=10000)
        previous = ""
        for expected_revision, event in enumerate(events, 1):
            if event["revision"] != expected_revision or event["previous_event_sha256"] != previous:
                return False
            expected = self._event_hash(
                event["revision"], event["actor"], event["action"],
                event["catalog_sha256"], previous,
            )
            if not hmac.compare_digest(expected, event["event_sha256"]):
                return False
            previous = event["event_sha256"]
        state = self.load()
        if state["revision"] != len(events):
            return False
        if not events:
            return state["catalog"] == new_catalog()
        current_hash = hashlib.sha256(
            self._serialize(state["catalog"]).encode("utf-8")
        ).hexdigest()
        return hmac.compare_digest(current_hash, events[-1]["catalog_sha256"])
