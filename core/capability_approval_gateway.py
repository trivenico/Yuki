"""Durable, request-bound one-shot approvals for central capability policy.

This gateway is an authorization primitive, not a sandbox. Every side-effecting
execution path must call it immediately before dispatch and must enforce its
decision. Approval credentials are never stored in plaintext.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from core.tool_policy import Decision, PolicyRequest, Risk


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    status: str
    policy_version: str
    matched_rule_ids: tuple[str, ...]
    approval_consumed: bool = False


class CapabilityApprovalGateway:
    """Evaluate central policy and consume exact-operation approvals atomically."""

    def __init__(
        self,
        database_path: str | Path,
        policy_engine: Any,
        *,
        authorize_approver: Callable[[str, PolicyRequest], bool],
        clock: Callable[[], float] = time.time,
        max_approval_ttl_seconds: int = 3600,
    ) -> None:
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be a non-empty path")
        if not callable(getattr(policy_engine, "evaluate", None)):
            raise TypeError("policy_engine must provide evaluate(request)")
        if not callable(authorize_approver):
            raise TypeError("a trusted approval authorization callback is required")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(max_approval_ttl_seconds) is not int or not 1 <= max_approval_ttl_seconds <= 86400:
            raise ValueError("max_approval_ttl_seconds must be between 1 and 86400")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.policy_engine = policy_engine
        self.authorize_approver = authorize_approver
        self.clock = clock
        self.max_approval_ttl_seconds = max_approval_ttl_seconds
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS capability_approvals (
                    token_sha256 TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    target TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL,
                    revoked_at REAL,
                    revoked_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_capability_approvals_expiry
                    ON capability_approvals(expires_at);
                CREATE TABLE IF NOT EXISTS capability_approval_requests (
                    request_id TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    target TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','expired','rejected')),
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    decided_at REAL,
                    decided_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_approval_requests_status_expiry
                    ON capability_approval_requests(status, expires_at);
                CREATE TABLE IF NOT EXISTS capability_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    target TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    matched_rules_json TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    occurred_at REAL NOT NULL
                );
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _request_digest(request: PolicyRequest) -> str:
        if not isinstance(request, PolicyRequest):
            raise TypeError("request must be a validated PolicyRequest")
        canonical = json.dumps(
            asdict(request), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
            default=lambda value: value.value if hasattr(value, "value") else value,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _token_digest(token: str) -> str:
        if not isinstance(token, str) or not 40 <= len(token) <= 256:
            raise ValueError("approval token is invalid")
        try:
            token.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("approval token is invalid") from error
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def _audit(self, db, event_type: str, request: PolicyRequest,
               request_digest: str, policy_version: str,
               matched_rule_ids: tuple[str, ...], outcome: str) -> None:
        db.execute(
            """INSERT INTO capability_audit
               (event_type,principal_id,capability,target,run_id,step_id,
                request_sha256,policy_version,matched_rules_json,outcome,occurred_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (event_type, request.principal_id, request.capability, request.target,
             request.run_id, request.step_id, request_digest, policy_version,
             json.dumps(list(matched_rule_ids), separators=(",", ":")),
             outcome, self.clock()),
        )

    def issue_approval(
        self, request: PolicyRequest, *, approved_by: str,
        ttl_seconds: int = 300,
    ) -> str:
        """Issue an opaque token only after trusted human approval authorization."""
        digest = self._request_digest(request)
        if not isinstance(approved_by, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", approved_by
        ):
            raise ValueError("approved_by must be a bounded authenticated identity")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= self.max_approval_ttl_seconds:
            raise ValueError("ttl_seconds is outside the configured approval lifetime")
        policy = self.policy_engine.evaluate(request)
        if policy.decision != Decision.REQUIRE_APPROVAL:
            raise PermissionError("the current policy does not permit an approval override")
        try:
            trusted = self.authorize_approver(approved_by, request)
        except Exception as error:
            raise PermissionError("approval authority could not be verified") from error
        if trusted is not True:
            raise PermissionError("approver is not authorized for this request")

        token = secrets.token_urlsafe(32)
        token_hash = self._token_digest(token)
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO capability_approvals
                   (token_sha256,request_sha256,request_json,policy_version,
                    principal_id,capability,target,run_id,step_id,created_by,
                    created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (token_hash, digest, json.dumps(asdict(request), sort_keys=True,
                 separators=(",", ":"), ensure_ascii=False), policy.policy_version,
                 request.principal_id, request.capability, request.target,
                 request.run_id, request.step_id, approved_by, now, now + ttl_seconds),
            )
            self._audit(db, "approval_issued", request, digest, policy.policy_version,
                        policy.matched_rule_ids, "issued")
            db.commit()
        return token


    def request_approval(self, request: PolicyRequest, *, ttl_seconds: int = 86400) -> str:
        """Persist an approval request; duplicate live requests are idempotent."""
        digest = self._request_digest(request)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("ttl_seconds is outside the configured request lifetime")
        policy = self.policy_engine.evaluate(request)
        if policy.decision != Decision.REQUIRE_APPROVAL:
            raise PermissionError("the current policy does not permit an approval request")
        now = self.clock()
        request_json = json.dumps(
            asdict(request), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            default=lambda value: value.value if hasattr(value, "value") else value,
        )
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE capability_approval_requests SET status='expired'
                   WHERE status='pending' AND expires_at<=?""", (now,),
            )
            db.execute(
                """UPDATE capability_approval_requests SET status='rejected',decided_at=?
                   WHERE request_sha256=? AND status='pending' AND policy_version<>?""",
                (now, digest, policy.policy_version),
            )
            existing = db.execute(
                """SELECT request_id FROM capability_approval_requests
                   WHERE request_sha256=? AND policy_version=?
                     AND status='pending' AND expires_at>?""",
                (digest, policy.policy_version, now),
            ).fetchone()
            if existing is not None:
                db.commit()
                return existing["request_id"]
            request_id = secrets.token_hex(16)
            db.execute(
                """INSERT INTO capability_approval_requests
                   (request_id,request_sha256,request_json,policy_version,principal_id,
                    capability,target,run_id,step_id,status,created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                (request_id, digest, request_json, policy.policy_version,
                 request.principal_id, request.capability, request.target,
                 request.run_id, request.step_id, now, now + ttl_seconds),
            )
            self._audit(db, "approval_requested", request, digest,
                        policy.policy_version, policy.matched_rule_ids, "pending")
            db.commit()
        return request_id

    def pending_requests(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        """Return redacted inbox metadata; never expose request arguments or credentials."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE capability_approval_requests SET status='expired'
                   WHERE status='pending' AND expires_at<=?""", (now,),
            )
            rows = db.execute(
                """SELECT request_id,principal_id,capability,target,run_id,step_id,
                          request_json,policy_version,created_at,expires_at
                   FROM capability_approval_requests
                   WHERE status='pending' AND expires_at>?
                   ORDER BY created_at,request_id LIMIT ?""",
                (now, limit),
            ).fetchall()
            db.commit()
        redacted = []
        for row in rows:
            item = dict(row)
            request_metadata = json.loads(item.pop("request_json"))
            # Only expose review metadata; never expose argument or claim digests.
            item["risk"] = request_metadata.get("risk")
            item["trigger"] = request_metadata.get("trigger")
            redacted.append(item)
        return tuple(redacted)

    def reject_request(self, request_id: str, *, rejected_by: str) -> bool:
        """Reject one pending request after validating the authenticated approver."""
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("request_id is invalid")
        if not isinstance(rejected_by, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", rejected_by
        ):
            raise ValueError("rejected_by must be a bounded authenticated identity")
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM capability_approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                db.commit()
                return False
            if row["expires_at"] <= now:
                db.execute(
                    """UPDATE capability_approval_requests SET status='expired',decided_at=?
                       WHERE request_id=? AND status='pending'""", (now, request_id),
                )
                db.commit()
                return False
            raw = json.loads(row["request_json"])
            raw["risk"] = Risk(raw["risk"])
            request = PolicyRequest(**raw)
            try:
                trusted = self.authorize_approver(rejected_by, request)
            except Exception as error:
                db.rollback()
                raise PermissionError("rejection authority could not be verified") from error
            if trusted is not True:
                db.rollback()
                raise PermissionError("rejector is not authorized for this request")
            changed = db.execute(
                """UPDATE capability_approval_requests SET status='rejected',
                   decided_at=?,decided_by=? WHERE request_id=? AND status='pending'""",
                (now, rejected_by, request_id),
            ).rowcount
            if changed == 1:
                digest = self._request_digest(request)
                policy = self.policy_engine.evaluate(request)
                self._audit(db, "approval_rejected", request, digest,
                            policy.policy_version, policy.matched_rule_ids, "rejected")
            db.commit()
            return changed == 1

    def approve_request(
        self, request_id: str, *, approved_by: str, ttl_seconds: int = 300,
    ) -> str:
        """Atomically approve one inbox item and mint its one-shot credential."""
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("request_id is invalid")
        if not isinstance(approved_by, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", approved_by
        ):
            raise ValueError("approved_by must be a bounded authenticated identity")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= self.max_approval_ttl_seconds:
            raise ValueError("ttl_seconds is outside the configured approval lifetime")
        token = secrets.token_urlsafe(32)
        token_hash = self._token_digest(token)
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM capability_approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                db.rollback()
                raise PermissionError("approval request is unavailable")
            if row["expires_at"] <= now:
                db.execute(
                    """UPDATE capability_approval_requests SET status='expired',decided_at=?
                       WHERE request_id=? AND status='pending'""", (now, request_id),
                )
                db.commit()
                raise PermissionError("approval request has expired")
            raw = json.loads(row["request_json"])
            raw["risk"] = Risk(raw["risk"])
            request = PolicyRequest(**raw)
            digest = self._request_digest(request)
            if not hmac.compare_digest(digest, row["request_sha256"]):
                db.rollback()
                raise PermissionError("approval request integrity check failed")
            policy = self.policy_engine.evaluate(request)
            if (policy.decision != Decision.REQUIRE_APPROVAL
                    or policy.policy_version != row["policy_version"]):
                db.execute(
                    """UPDATE capability_approval_requests SET status='rejected',
                       decided_at=?,decided_by=? WHERE request_id=? AND status='pending'""",
                    (now, approved_by, request_id),
                )
                self._audit(db, "approval_rejected", request, digest,
                            policy.policy_version, policy.matched_rule_ids, "policy_changed")
                db.commit()
                raise PermissionError("approval request is stale under the current policy")
            try:
                trusted = self.authorize_approver(approved_by, request)
            except Exception as error:
                db.rollback()
                raise PermissionError("approval authority could not be verified") from error
            if trusted is not True:
                db.rollback()
                raise PermissionError("approver is not authorized for this request")
            changed = db.execute(
                """UPDATE capability_approval_requests SET status='approved',
                   decided_at=?,decided_by=? WHERE request_id=? AND status='pending'""",
                (now, approved_by, request_id),
            ).rowcount
            if changed != 1:
                db.rollback()
                raise PermissionError("approval request is no longer pending")
            db.execute(
                """INSERT INTO capability_approvals
                   (token_sha256,request_sha256,request_json,policy_version,principal_id,
                    capability,target,run_id,step_id,created_by,created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (token_hash, digest, row["request_json"], policy.policy_version,
                 request.principal_id, request.capability, request.target,
                 request.run_id, request.step_id, approved_by, now, now + ttl_seconds),
            )
            self._audit(db, "approval_issued", request, digest,
                        policy.policy_version, policy.matched_rule_ids, "issued_from_inbox")
            db.commit()
        return token

    def _request_json_for_binding(self, request_id: str) -> str:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT request_json FROM capability_approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise PermissionError("approval request binding is unavailable")
        return row["request_json"]

    def approval_delivery_binding(self, request_id: str, token: str) -> dict[str, Any]:
        """Return verified task metadata for trusted credential delivery only."""
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("request_id is invalid")
        token_hash = self._token_digest(token)
        now = self.clock()
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT r.principal_id,r.capability,r.target,r.run_id,r.step_id,
                          r.policy_version,r.expires_at,r.status,
                          a.token_sha256,a.expires_at AS credential_expires_at,
                          a.consumed_at,a.revoked_at
                   FROM capability_approval_requests r
                   JOIN capability_approvals a
                     ON a.request_sha256=r.request_sha256
                    AND a.policy_version=r.policy_version
                   WHERE r.request_id=? AND a.token_sha256=?""",
                (request_id, token_hash),
            ).fetchone()
        if (row is None or row["status"] != "approved"
                or row["credential_expires_at"] <= now
                or row["expires_at"] <= now
                or row["consumed_at"] is not None or row["revoked_at"] is not None):
            raise PermissionError("approved credential is unavailable for delivery")
        return {
            "principal_id": row["principal_id"], "capability": row["capability"],
            "target": row["target"], "run_id": row["run_id"], "step_id": row["step_id"],
            "policy_version": row["policy_version"],
            "claim_token_sha256": json.loads(
                self._request_json_for_binding(request_id)
            ).get("claim_token_sha256"),
            "expires_at": min(row["expires_at"], row["credential_expires_at"]),
        }

    def approval_delivery_binding_by_digest(self, request_id: str, credential_sha256: str) -> dict[str, Any]:
        """Return immutable task metadata for a stored credential digest."""
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("request_id is invalid")
        if not isinstance(credential_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", credential_sha256):
            raise ValueError("credential digest is invalid")
        now = self.clock()
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT r.principal_id,r.capability,r.target,r.run_id,r.step_id,
                          r.policy_version,r.expires_at,r.status,
                          a.expires_at AS credential_expires_at,a.consumed_at,a.revoked_at
                   FROM capability_approval_requests r
                   JOIN capability_approvals a
                     ON a.request_sha256=r.request_sha256
                    AND a.policy_version=r.policy_version
                   WHERE r.request_id=? AND a.token_sha256=?""",
                (request_id, credential_sha256),
            ).fetchone()
        if (row is None or row["status"] != "approved"
                or row["credential_expires_at"] <= now or row["expires_at"] <= now
                or row["consumed_at"] is not None or row["revoked_at"] is not None):
            raise PermissionError("approved credential binding is unavailable")
        return {
            "principal_id": row["principal_id"], "capability": row["capability"],
            "target": row["target"], "run_id": row["run_id"], "step_id": row["step_id"],
            "policy_version": row["policy_version"],
            "claim_token_sha256": json.loads(
                self._request_json_for_binding(request_id)
            ).get("claim_token_sha256"),
            "expires_at": min(row["expires_at"], row["credential_expires_at"]),
        }

    def authorize(
        self, request: PolicyRequest, *, approval_token: str | None = None,
    ) -> CapabilityDecision:
        """Evaluate policy; consume a matching approval once, in the same transaction."""
        digest = self._request_digest(request)
        try:
            policy = self.policy_engine.evaluate(request)
            if policy.decision == Decision.ALLOW:
                outcome = "allowed"
                with closing(self._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._audit(db, "authorization", request, digest,
                                policy.policy_version, policy.matched_rule_ids, outcome)
                    db.commit()
                decision = CapabilityDecision(True, outcome, policy.policy_version,
                                              policy.matched_rule_ids)
            elif policy.decision == Decision.REQUIRE_APPROVAL and approval_token is not None:
                token_hash = self._token_digest(approval_token)
                now = self.clock()
                with closing(self._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute(
                        "SELECT * FROM capability_approvals WHERE token_sha256=?",
                        (token_hash,),
                    ).fetchone()
                    valid = (
                        row is not None
                        and hmac.compare_digest(row["request_sha256"], digest)
                        and row["principal_id"] == request.principal_id
                        and row["capability"] == request.capability
                        and row["target"] == request.target
                        and row["run_id"] == request.run_id
                        and row["step_id"] == request.step_id
                        and row["policy_version"] == policy.policy_version
                        and row["expires_at"] > now
                        and row["consumed_at"] is None
                        and row["revoked_at"] is None
                    )
                    if valid:
                        changed = db.execute(
                            """UPDATE capability_approvals SET consumed_at=?
                               WHERE token_sha256=? AND consumed_at IS NULL
                               AND revoked_at IS NULL AND expires_at>?""",
                            (now, token_hash, now),
                        ).rowcount
                        valid = changed == 1
                    outcome = "approval_consumed" if valid else "approval_rejected"
                    self._audit(db, "authorization", request, digest,
                                policy.policy_version, policy.matched_rule_ids, outcome)
                    db.commit()
                decision = CapabilityDecision(
                    bool(valid), outcome if valid else "approval_invalid",
                    policy.policy_version, policy.matched_rule_ids,
                    approval_consumed=bool(valid),
                )
            else:
                outcome = policy.decision.value
                with closing(self._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._audit(db, "authorization", request, digest,
                                policy.policy_version, policy.matched_rule_ids, outcome)
                    db.commit()
                decision = CapabilityDecision(
                    False, outcome, policy.policy_version, policy.matched_rule_ids,
                )
            return decision
        except (TypeError, ValueError):
            raise
        except Exception:
            # Policy and storage failures never become permission to execute.
            return CapabilityDecision(False, "gateway_error", "unavailable", ())

    def revoke(self, approval_token: str, *, revoked_by: str) -> bool:
        """Revoke an unconsumed approval; caller must authenticate revoked_by."""
        token_hash = self._token_digest(approval_token)
        if not isinstance(revoked_by, str) or not revoked_by.strip() or len(revoked_by) > 256:
            raise ValueError("revoked_by must be authenticated bounded identity text")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM capability_approvals WHERE token_sha256=?",
                (token_hash,),
            ).fetchone()
            if row is None or row["consumed_at"] is not None or row["revoked_at"] is not None:
                db.commit()
                return False
            raw = json.loads(row["request_json"])
            raw["risk"] = Risk(raw["risk"])
            request = PolicyRequest(**raw)
            try:
                trusted = self.authorize_approver(revoked_by, request)
            except Exception as error:
                raise PermissionError("revocation authority could not be verified") from error
            if trusted is not True:
                raise PermissionError("revoker is not authorized for this request")
            now = self.clock()
            changed = db.execute(
                """UPDATE capability_approvals SET revoked_at=?,revoked_by=?
                   WHERE token_sha256=? AND consumed_at IS NULL AND revoked_at IS NULL""",
                (now, revoked_by, token_hash),
            ).rowcount
            if changed == 1:
                self._audit(db, "approval_revoked", request, row["request_sha256"],
                            row["policy_version"], (), "revoked")
            db.commit()
            return changed == 1

    def audit_events(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM capability_audit ORDER BY event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple({
            "event_id": row["event_id"], "event_type": row["event_type"],
            "principal_id": row["principal_id"], "capability": row["capability"],
            "target": row["target"], "run_id": row["run_id"],
            "step_id": row["step_id"], "request_sha256": row["request_sha256"],
            "policy_version": row["policy_version"],
            "matched_rule_ids": tuple(json.loads(row["matched_rules_json"])),
            "outcome": row["outcome"], "occurred_at": row["occurred_at"],
        } for row in rows)
