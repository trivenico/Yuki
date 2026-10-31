"""Safe synchronization adapter for Yuki's data-only skill packs.

Only validated JSON skill-pack manifests are synchronized. Python modules,
shell scripts, plugin executables, approvals and credentials are never shared.
Imported packs are always raised to high risk on the receiving device.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.portable_paths import data_root
from core.skill_packs import installed_skill_directory, read_skill_pack, validate_skill_pack
from core.sync_client import SyncClientError
from core.sync_contract import validate_sync_event


class SkillPackSyncAdapter:
    def __init__(self, state_path: str | Path | None = None,
                 pack_directory: str | Path | None = None):
        self.state_path = Path(state_path) if state_path is not None else (
            data_root() / ".yuki" / "skill_sync.sqlite3"
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.pack_directory = (
            Path(pack_directory).expanduser().resolve()
            if pack_directory is not None else installed_skill_directory()
        )
        self.pack_directory.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.state_path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS applied_events (
                    event_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS queued_packs (
                    name TEXT PRIMARY KEY,
                    manifest_hash TEXT NOT NULL,
                    event_json TEXT NOT NULL
                )
            """)

    @staticmethod
    def _event_id(device_id: str, name: str) -> str:
        digest = hashlib.sha256((device_id + ":skill:" + name).encode()).hexdigest()[:32]
        return "event_" + digest

    def preview(self) -> dict[str, int]:
        packs = 0
        for path in sorted(self.pack_directory.glob("*.json")):
            try:
                read_skill_pack(path)
                packs += 1
            except (OSError, ValueError):
                continue
        return {"installed_data_only_packs": packs}

    def queue_installed(self, device_id: str,
                        enqueue: Callable[[dict[str, Any]], bool]) -> dict[str, int]:
        if not isinstance(device_id, str) or len(device_id) < 8:
            raise SyncClientError("A paired device is required before queuing skill packs.")
        queued = skipped = 0
        for path in sorted(self.pack_directory.glob("*.json")):
            try:
                manifest = read_skill_pack(path)
                if path.name != manifest["name"] + ".json":
                    raise ValueError("Skill pack filename does not match its manifest.")
                # The sync contract only carries data-only manifest fields.
                payload = validate_skill_pack(manifest)
                manifest_hash = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                with sqlite3.connect(self.state_path, timeout=10) as db:
                    db.execute("BEGIN IMMEDIATE")
                    prior = db.execute(
                        "SELECT manifest_hash,event_json FROM queued_packs WHERE name=?",
                        (payload["name"],),
                    ).fetchone()
                    if prior and prior[0] != manifest_hash:
                        raise SyncClientError(
                            "Skill pack updates are not supported yet; a changed pack needs a reviewed revision migration."
                        )
                    if prior:
                        event = json.loads(prior[1])
                    else:
                        event = {
                            "event_id": self._event_id(device_id, payload["name"]),
                            "device_id": device_id,
                            "collection": "skill_metadata",
                            "object_id": "skill_" + hashlib.sha256(payload["name"].encode()).hexdigest()[:32],
                            "revision": 1,
                            "occurred_at": datetime.now(timezone.utc).isoformat(),
                            "operation": "upsert",
                            "payload": payload,
                        }
                        validate_sync_event(event)
                        db.execute(
                            "INSERT INTO queued_packs(name,manifest_hash,event_json) VALUES(?,?,?)",
                            (payload["name"], manifest_hash,
                             json.dumps(event, ensure_ascii=False, sort_keys=True)),
                        )
                    db.commit()
                if event["device_id"] != device_id:
                    raise SyncClientError("Skill pack event belongs to a different paired device.")
                if enqueue(event):
                    queued += 1
                else:
                    skipped += 1
            except (OSError, ValueError) as exc:
                raise SyncClientError("An installed skill pack failed sync validation.") from exc
        return {"queued": queued, "already_queued": skipped}

    def apply_remote(self, raw_event: dict[str, Any]) -> str:
        event = validate_sync_event(raw_event).to_dict()
        if event["collection"] != "skill_metadata" or event["operation"] != "upsert":
            raise SyncClientError("Only skill metadata upserts are supported.")
        payload = event["payload"]
        try:
            manifest = validate_skill_pack(payload)
        except ValueError as exc:
            raise SyncClientError("Remote skill pack manifest is invalid.") from exc
        expected_id = "skill_" + hashlib.sha256(manifest["name"].encode()).hexdigest()[:32]
        if event["object_id"] != expected_id or event["revision"] != 1:
            raise SyncClientError("Remote skill pack identity or revision is invalid.")

        # A remote pack is untrusted guidance, never an authorization grant.
        manifest["risk"] = "high"
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        digest = hashlib.sha256(json.dumps(event, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        target = self.pack_directory / (manifest["name"] + ".json")
        with sqlite3.connect(self.state_path, timeout=10) as db:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload_hash FROM applied_events WHERE event_id=?", (event["event_id"],)
            ).fetchone()
            if prior:
                if prior[0] != digest:
                    raise SyncClientError("Skill event ID was reused with different content.")
                db.commit()
                return "duplicate"
            if target.exists():
                try:
                    existing = read_skill_pack(target)
                except (OSError, ValueError) as exc:
                    raise SyncClientError("An existing local skill pack is invalid; refusing overwrite.") from exc
                existing["risk"] = "high"
                if existing != manifest:
                    raise SyncClientError("A different local skill pack already uses this name.")
                result = "already_installed"
            else:
                fd, temporary = tempfile.mkstemp(
                    prefix=".skill-sync-", suffix=".tmp", dir=str(self.pack_directory)
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if target.exists():
                        raise SyncClientError("Skill pack appeared during synchronization; retry safely.")
                    os.replace(temporary, target)
                finally:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
                result = "installed"
            db.execute(
                "INSERT INTO applied_events(event_id,payload_hash) VALUES(?,?)",
                (event["event_id"], digest),
            )
            db.commit()
        return result
