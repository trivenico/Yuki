"""Evidence-gated, human-approved remediation planning for reviewer incidents.

Plans are advisory records. Approval records authorization to proceed to a
separate executor; this module never executes privileged operations.
"""
from __future__ import annotations

from contextlib import closing

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
from pathlib import Path
from typing import Callable, Iterable

from automation.reviewer_incident_intelligence import IncidentIntelligenceReport


@dataclass(frozen=True)
class RemediationPlan:
    plan_id: str
    finding_code: str
    recommendation_code: str
    priority: str
    risk: str
    description: str
    required_evidence: tuple[str, ...]
    reversible: bool
    approval_state: str
    created_at: str


@dataclass(frozen=True)
class RemediationDecision:
    plan_id: str
    state: str
    actor_id: str
    reason_code: str
    occurred_at: str


class ReviewerRemediationPlanner:
    """Turns structured intelligence findings into auditable gated plans."""

    _REQUIREMENTS = {
        "review_reviewer_capacity": ("reviewer_pool_snapshot", "eligible_reviewer_policy"),
        "verify_reviewer_eligibility": ("reviewer_eligibility_evidence",),
        "inspect_replacement_resolver": ("resolver_error_class", "resolver_dependency_health"),
        "verify_resolver_dependencies": ("resolver_dependency_health", "timeout_configuration"),
        "audit_reviewer_identity_policy": ("identity_validation_policy", "authorization_test_results"),
        "inspect_concurrent_recovery": ("recovery_controller_instances", "task_version_history"),
        "review_lease_ttl": ("worker_heartbeat_history", "lease_ttl_configuration"),
        "monitor_recovery_rate": ("recovery_outcome_window",),
        "inspect_incident_evidence": ("incident_event_history",),
        "review_dead_letter_backlog": ("dead_letter_event_history", "operator_impact_assessment"),
        "inspect_worker_health": ("worker_heartbeat_history", "process_restart_history", "resource_metrics"),
    }

    _RISK = {
        "review_reviewer_capacity": ("medium", True),
        "verify_reviewer_eligibility": ("medium", True),
        "inspect_replacement_resolver": ("high", True),
        "verify_resolver_dependencies": ("medium", True),
        "audit_reviewer_identity_policy": ("high", True),
        "inspect_concurrent_recovery": ("medium", True),
        "review_lease_ttl": ("high", True),
        "monitor_recovery_rate": ("low", True),
        "inspect_incident_evidence": ("low", True),
        "review_dead_letter_backlog": ("high", True),
        "inspect_worker_health": ("medium", True),
    }

    def build(self, report: IncidentIntelligenceReport) -> tuple[RemediationPlan, ...]:
        if not isinstance(report, IncidentIntelligenceReport):
            raise TypeError("incident_intelligence_report_required")
        recommendations = {x.code: x for x in report.recommendations}
        plans = []
        seen = set()
        for finding in report.findings:
            for code in finding.recommendation_codes:
                if code in seen or code not in recommendations:
                    continue
                seen.add(code)
                rec = recommendations[code]
                risk, reversible = self._RISK.get(code, ("high", False))
                required = self._REQUIREMENTS.get(code, ("supporting_incident_evidence",))
                plans.append(RemediationPlan(
                    plan_id="rem-" + __import__("uuid").uuid4().hex,
                    finding_code=finding.code,
                    recommendation_code=code,
                    priority=rec.priority,
                    risk=risk,
                    description=rec.description,
                    required_evidence=required,
                    reversible=reversible,
                    approval_state="pending",
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
        return tuple(plans)


class ReviewerRemediationPlanStore:
    """SQLite plan, decision and execution-receipt journal with host authorization."""

    def __init__(self, database: str | Path, *,
                 authorize: Callable[[str, str, str], bool]) -> None:
        if not callable(authorize):
            raise TypeError("remediation_plan_authorizer_required")
        self.database = str(database)
        self.authorize = authorize
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_remediation_plans (
                plan_id TEXT PRIMARY KEY, finding_code TEXT NOT NULL,
                recommendation_code TEXT NOT NULL, priority TEXT NOT NULL,
                risk TEXT NOT NULL, description TEXT NOT NULL,
                required_evidence_json TEXT NOT NULL, reversible INTEGER NOT NULL,
                approval_state TEXT NOT NULL CHECK(approval_state IN ('pending','approved','rejected','executed')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_remediation_decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL REFERENCES reviewer_remediation_plans(plan_id),
                state TEXT NOT NULL, actor_id TEXT NOT NULL,
                reason_code TEXT NOT NULL, occurred_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_remediation_receipts (
                receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL REFERENCES reviewer_remediation_plans(plan_id),
                executor_id TEXT NOT NULL, outcome TEXT NOT NULL,
                evidence_digest TEXT NOT NULL, occurred_at TEXT NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _stamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _identifier(value: str, code: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
            raise ValueError(code)
        return value

    def _allow(self, actor: str, action: str, plan: str) -> None:
        if not self.authorize(actor, action, plan):
            raise PermissionError("remediation plan access denied")

    def save(self, *, actor_id: str, plans: Iterable[RemediationPlan]) -> int:
        actor = self._identifier(actor_id, "invalid_remediation_actor")
        rows = tuple(plans)
        if len(rows) > 500 or any(not isinstance(x, RemediationPlan) for x in rows):
            raise ValueError("invalid_remediation_plan_batch")
        saved = 0
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for plan in rows:
                plan_id = self._identifier(plan.plan_id, "invalid_remediation_plan_id")
                self._allow(actor, "review:remediation:create", plan_id)
                if plan.approval_state != "pending":
                    raise ValueError("new_remediation_plan_must_be_pending")
                now = self._stamp()
                db.execute("""INSERT INTO reviewer_remediation_plans
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                    plan_id, plan.finding_code, plan.recommendation_code,
                    plan.priority, plan.risk, plan.description,
                    json.dumps(plan.required_evidence), int(plan.reversible),
                    "pending", plan.created_at, now,
                ))
                saved += 1
        return saved

    def decide(self, *, actor_id: str, plan_id: str, decision: str,
               reason_code: str) -> RemediationDecision:
        actor = self._identifier(actor_id, "invalid_remediation_actor")
        plan = self._identifier(plan_id, "invalid_remediation_plan_id")
        if decision not in ("approved", "rejected"):
            raise ValueError("invalid_remediation_decision")
        if not isinstance(reason_code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", reason_code):
            raise ValueError("invalid_remediation_reason")
        action = "review:remediation:approve" if decision == "approved" else "review:remediation:reject"
        self._allow(actor, action, plan)
        stamp = self._stamp()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT approval_state FROM reviewer_remediation_plans WHERE plan_id=?", (plan,)).fetchone()
            if row is None:
                raise KeyError("remediation plan not found")
            if row["approval_state"] != "pending":
                raise ValueError("remediation_plan_already_decided")
            db.execute("UPDATE reviewer_remediation_plans SET approval_state=?,updated_at=? WHERE plan_id=?",
                       (decision, stamp, plan))
            db.execute("""INSERT INTO reviewer_remediation_decisions
                (plan_id,state,actor_id,reason_code,occurred_at) VALUES (?,?,?,?,?)""",
                (plan, decision, actor, reason_code, stamp))
        return RemediationDecision(plan, decision, actor, reason_code, stamp)

    def record_execution_receipt(self, *, actor_id: str, plan_id: str,
                                 outcome: str, evidence_digest: str) -> None:
        actor = self._identifier(actor_id, "invalid_remediation_executor")
        plan = self._identifier(plan_id, "invalid_remediation_plan_id")
        if outcome not in ("succeeded", "failed", "rolled_back"):
            raise ValueError("invalid_remediation_execution_outcome")
        if not isinstance(evidence_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", evidence_digest):
            raise ValueError("invalid_remediation_evidence_digest")
        self._allow(actor, "review:remediation:receipt", plan)
        stamp = self._stamp()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT approval_state FROM reviewer_remediation_plans WHERE plan_id=?", (plan,)).fetchone()
            if row is None:
                raise KeyError("remediation plan not found")
            if row["approval_state"] != "approved":
                raise ValueError("remediation_plan_not_approved")
            db.execute("""INSERT INTO reviewer_remediation_receipts
                (plan_id,executor_id,outcome,evidence_digest,occurred_at)
                VALUES (?,?,?,?,?)""", (plan, actor, outcome, evidence_digest, stamp))
            state = "executed" if outcome == "succeeded" else "approved"
            db.execute("UPDATE reviewer_remediation_plans SET approval_state=?,updated_at=? WHERE plan_id=?",
                       (state, stamp, plan))


__all__ = [
    "RemediationPlan", "RemediationDecision", "ReviewerRemediationPlanner",
    "ReviewerRemediationPlanStore",
]
