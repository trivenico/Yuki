"""Evidence-first opportunity research and coach planning primitives for Yuki.

This module stores research candidates and evidence supplied by trusted callers.
It does not scrape websites, guarantee earnings, make purchases, or contact people.
Treat page text, emails, and model-generated summaries as untrusted input.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


CATEGORIES = {
    "freelance", "remote_job", "digital_product", "ecommerce",
    "content", "affiliate", "research_other",
}
STAGES = {"new", "researching", "validated", "rejected", "in_progress", "completed"}
VERDICTS = {"promising", "needs_research", "avoid"}
EVIDENCE_KINDS = {"official", "first_party", "independent", "user_note"}
SCORING_FIELDS = ("demand", "fit", "startup_cost", "time_to_first_revenue", "risk", "evidence_quality")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _required(value: str, field: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return value


def _score(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise ValueError(f"{field} must be an integer from 1 to 5")
    return value


def validate_source_url(url: str) -> str:
    """Accept only HTTPS public-looking source URLs; never fetches the URL."""
    url = _required(url, "url", 2048)
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ValueError("source URL must use HTTPS and include a hostname")
    host = parts.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("local hostnames are not valid evidence sources")
    if parts.username or parts.password:
        raise ValueError("credentials must not be embedded in source URLs")
    if parts.port is not None and parts.port not in {443}:
        raise ValueError("only standard HTTPS port 443 is allowed")
    return url


@dataclass(frozen=True)
class OpportunityScore:
    demand: int
    fit: int
    startup_cost: int
    time_to_first_revenue: int
    risk: int
    evidence_quality: int

    def __post_init__(self):
        for field in SCORING_FIELDS:
            _score(getattr(self, field), field)

    @property
    def weighted_score(self) -> float:
        # Startup cost, speed, and risk are scored positively: 5 means low cost,
        # faster potential revenue, or lower risk respectively.
        weights = {
            "demand": 0.22, "fit": 0.22, "startup_cost": 0.12,
            "time_to_first_revenue": 0.16, "risk": 0.16, "evidence_quality": 0.12,
        }
        return round(sum(getattr(self, key) * weight for key, weight in weights.items()) * 20, 1)


@dataclass(frozen=True)
class ResearchVerdict:
    opportunity_id: int
    weighted_score: float
    verdict: str
    rationale: str
    next_step: str
    evidence_count: int
    updated_at: str


class OpportunityResearch:
    """Persistent research queue with provenance, scoring, and stage tracking."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    estimated_startup_cost REAL NOT NULL CHECK(estimated_startup_cost >= 0),
                    currency TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opportunity_id INTEGER NOT NULL REFERENCES research_opportunities(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    confidence INTEGER NOT NULL CHECK(confidence BETWEEN 1 AND 5),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_assessments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opportunity_id INTEGER NOT NULL REFERENCES research_opportunities(id) ON DELETE CASCADE,
                    demand INTEGER NOT NULL,
                    fit INTEGER NOT NULL,
                    startup_cost INTEGER NOT NULL,
                    time_to_first_revenue INTEGER NOT NULL,
                    risk INTEGER NOT NULL,
                    evidence_quality INTEGER NOT NULL,
                    weighted_score REAL NOT NULL,
                    verdict TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    next_step TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_stage ON research_opportunities(stage);
                CREATE INDEX IF NOT EXISTS idx_research_evidence_opportunity ON research_evidence(opportunity_id);
            """)
            db.execute("PRAGMA foreign_keys = ON")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def add_opportunity(self, title: str, category: str, audience: str, hypothesis: str,
                        estimated_startup_cost: float = 0, currency: str = "USD") -> int:
        title = _required(title, "title")
        audience = _required(audience, "audience")
        hypothesis = _required(hypothesis, "hypothesis", 3000)
        if category not in CATEGORIES:
            raise ValueError("unsupported opportunity category")
        if isinstance(estimated_startup_cost, bool) or not isinstance(estimated_startup_cost, (int, float)) or estimated_startup_cost < 0:
            raise ValueError("estimated_startup_cost must be non-negative")
        cost = float(estimated_startup_cost)
        if cost != cost or cost in (float("inf"), float("-inf")):
            raise ValueError("estimated_startup_cost must be finite")
        currency = _required(currency, "currency", 3).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        now = _now()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO research_opportunities
                (title,category,audience,hypothesis,estimated_startup_cost,currency,stage,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (title, category, audience, hypothesis, round(cost, 2), currency, "new", now, now),
            )
            return int(cursor.lastrowid)

    def add_evidence(self, opportunity_id: int, kind: str, source_url: str,
                     claim: str, observed_at: str, confidence: int) -> int:
        if kind not in EVIDENCE_KINDS:
            raise ValueError("unsupported evidence kind")
        source_url = validate_source_url(source_url)
        claim = _required(claim, "claim", 3000)
        observed_at = _required(observed_at, "observed_at", 80)
        confidence = _score(confidence, "confidence")
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM research_opportunities WHERE id=?", (int(opportunity_id),)).fetchone()
            if not exists:
                raise KeyError("opportunity not found")
            cursor = db.execute(
                """INSERT INTO research_evidence
                (opportunity_id,kind,source_url,claim,observed_at,confidence,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (int(opportunity_id), kind, source_url, claim, observed_at, confidence, _now()),
            )
            db.execute("UPDATE research_opportunities SET updated_at=? WHERE id=?", (_now(), int(opportunity_id)))
            return int(cursor.lastrowid)

    def assess(self, opportunity_id: int, score: OpportunityScore, rationale: str,
               next_step: str) -> ResearchVerdict:
        rationale = _required(rationale, "rationale", 3000)
        next_step = _required(next_step, "next_step", 1000)
        with self._connect() as db:
            row = db.execute("SELECT stage FROM research_opportunities WHERE id=?", (int(opportunity_id),)).fetchone()
            if row is None:
                raise KeyError("opportunity not found")
            evidence_count = int(db.execute(
                "SELECT COUNT(*) AS n FROM research_evidence WHERE opportunity_id=?",
                (int(opportunity_id),),
            ).fetchone()["n"])
            # Evidence count is a separate minimum gate: a high subjective score
            # alone must not label an unresearched idea as promising.
            if evidence_count < 2:
                verdict = "needs_research"
            elif score.weighted_score >= 75 and score.evidence_quality >= 3 and score.risk >= 3:
                verdict = "promising"
            elif score.risk <= 2 or score.weighted_score < 45:
                verdict = "avoid"
            else:
                verdict = "needs_research"
            now = _now()
            db.execute(
                """INSERT INTO research_assessments
                (opportunity_id,demand,fit,startup_cost,time_to_first_revenue,risk,evidence_quality,
                 weighted_score,verdict,rationale,next_step,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(opportunity_id), score.demand, score.fit, score.startup_cost,
                 score.time_to_first_revenue, score.risk, score.evidence_quality,
                 score.weighted_score, verdict, rationale, next_step, now),
            )
            stage = "validated" if verdict == "promising" else ("rejected" if verdict == "avoid" else "researching")
            db.execute("UPDATE research_opportunities SET stage=?,updated_at=? WHERE id=?", (stage, now, int(opportunity_id)))
        return ResearchVerdict(int(opportunity_id), score.weighted_score, verdict, rationale, next_step, evidence_count, now)

    def list_opportunities(self, stage: Optional[str] = None) -> list[dict]:
        if stage is not None and stage not in STAGES:
            raise ValueError("unsupported opportunity stage")
        with self._connect() as db:
            if stage is None:
                rows = db.execute("SELECT * FROM research_opportunities ORDER BY updated_at DESC,id DESC").fetchall()
            else:
                rows = db.execute("SELECT * FROM research_opportunities WHERE stage=? ORDER BY updated_at DESC,id DESC", (stage,)).fetchall()
        return [dict(row) for row in rows]

    def evidence_for(self, opportunity_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,kind,source_url,claim,observed_at,confidence,created_at FROM research_evidence WHERE opportunity_id=? ORDER BY id",
                (int(opportunity_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_assessment(self, opportunity_id: int) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_assessments WHERE opportunity_id=? ORDER BY id DESC LIMIT 1",
                (int(opportunity_id),),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def coach_plan(available_minutes: int = 240, *, energy: str = "normal") -> list[dict]:
        """Return a practical plan; no external actions are taken."""
        if isinstance(available_minutes, bool) or not isinstance(available_minutes, int) or not 15 <= available_minutes <= 960:
            raise ValueError("available_minutes must be between 15 and 960")
        if energy not in {"low", "normal", "high"}:
            raise ValueError("energy must be low, normal, or high")
        if energy == "low":
            blocks = [("Review one promising lead", .25), ("Collect one reliable source", .30),
                      ("Complete one small portfolio task", .35), ("Write tomorrow's first action", .10)]
        else:
            blocks = [("Research demand and buyer problems", .25), ("Validate sources and competing offers", .25),
                      ("Build a small proof-of-skill or offer", .30), ("Prepare an outreach draft for review", .10),
                      ("Log evidence and choose tomorrow's next step", .10)]
        plan = []
        allocated = 0
        for i, (task, fraction) in enumerate(blocks):
            minutes = available_minutes - allocated if i == len(blocks) - 1 else int(available_minutes * fraction)
            allocated += minutes
            plan.append({"task": task, "minutes": minutes, "requires_approval": "outreach" in task.lower()})
        return plan

    def export_json(self) -> str:
        """Export research data for user-controlled backup or migration."""
        with self._connect() as db:
            opportunities = [dict(row) for row in db.execute("SELECT * FROM research_opportunities ORDER BY id")]
            evidence = [dict(row) for row in db.execute("SELECT * FROM research_evidence ORDER BY id")]
            assessments = [dict(row) for row in db.execute("SELECT * FROM research_assessments ORDER BY id")]
        return json.dumps({"opportunities": opportunities, "evidence": evidence, "assessments": assessments}, ensure_ascii=False, indent=2)
