"""Opt-in local daily evolution cycle.

This module analyzes only explicitly recorded, bounded observations. It proposes
habits/workflow improvements for review; it never edits code/config, executes tools,
calls a model/network, or trains a model.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),
    re.compile(r"(?i)\b(api[_ -]?key|password|token|secret)\s*[:=]\s*\S+"),
)
_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "were", "was", "are",
         "have", "has", "had", "not", "but", "you", "your", "yuki", "into", "when",
         "then", "than", "can", "could", "would", "should", "please", "need", "using"}


def redact_secrets(value: str) -> str:
    """Best-effort redact common credential formats before local persistence."""
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


class DailyEvolutionCycle:
    MAX_OBSERVATIONS = 2000
    MAX_OBSERVATION_CHARS = 1200
    MAX_PROPOSALS = 500

    def __init__(self, workspace: str | Path):
        root = Path(workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.workspace = root
        self.observations_path = root / "yuki_evolution_observations.json"
        self.state_path = root / "yuki_evolution_state.json"
        self.report_path = root / "yuki_evolution_report.json"
        self._lock = RLock()

    def _read_list(self, path: Path, limit: int) -> list:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Evolution data at {path.name} is unreadable; nothing was overwritten.") from exc
        if not isinstance(value, list) or len(value) > limit:
            raise RuntimeError(f"Evolution data at {path.name} has an invalid structure or exceeds limits.")
        return value

    def _atomic_write(self, path: Path, value) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=".yuki-evolution-", suffix=".tmp", dir=str(self.workspace))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def record(self, category: str, summary: str, *, occurred_at: datetime | None = None) -> dict:
        if category not in {"task", "feedback", "lesson", "failure", "preference"}:
            raise ValueError("category must be task, feedback, lesson, failure, or preference.")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > self.MAX_OBSERVATION_CHARS:
            raise ValueError(f"summary must be 1-{self.MAX_OBSERVATION_CHARS} characters.")
        stamp = occurred_at or datetime.now(timezone.utc)
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware.")
        with self._lock:
            items = self._read_list(self.observations_path, self.MAX_OBSERVATIONS)
            if len(items) >= self.MAX_OBSERVATIONS:
                raise ValueError("Evolution observation log is full; archive or clear it before adding more.")
            item = {"id": os.urandom(8).hex(), "category": category,
                    "summary": redact_secrets(summary.strip()),
                    "occurred_at": stamp.astimezone(timezone.utc).isoformat()}
            items.append(item)
            self._atomic_write(self.observations_path, items)
            return dict(item)

    def run_cycle(self, *, now: datetime | None = None, lookback_days: int = 7) -> dict:
        current = now or datetime.now(timezone.utc)
        if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or not 1 <= lookback_days <= 30:
            raise ValueError("lookback_days must be between 1 and 30.")
        current = current.astimezone(timezone.utc)
        cutoff = current - timedelta(days=lookback_days)
        with self._lock:
            observations = self._read_list(self.observations_path, self.MAX_OBSERVATIONS)
            recent = []
            for item in observations:
                if not isinstance(item, dict) or item.get("category") not in {"task", "feedback", "lesson", "failure", "preference"}:
                    continue
                try:
                    stamp = datetime.fromisoformat(item.get("occurred_at", ""))
                    if stamp.tzinfo is None or stamp.astimezone(timezone.utc) < cutoff:
                        continue
                except (TypeError, ValueError):
                    continue
                summary = item.get("summary")
                if isinstance(summary, str):
                    recent.append({**item, "summary": redact_secrets(summary)[:self.MAX_OBSERVATION_CHARS]})

            failures = [x for x in recent if x["category"] == "failure"]
            feedback = [x for x in recent if x["category"] == "feedback"]
            lessons = [x for x in recent if x["category"] in {"lesson", "preference"}]
            counts = Counter()
            for item in recent:
                words = {word.casefold() for word in _WORDS.findall(item["summary"])} - _STOP
                counts.update(words)
            repeated = [(word, count) for word, count in counts.most_common(8) if count >= 3]
            proposals = []
            if failures:
                proposals.append({"id": "review-failures-" + current.strftime("%Y%m%d"),
                                  "kind": "review", "status": "pending_review",
                                  "title": "Review recent failures",
                                  "detail": f"{len(failures)} explicitly logged failure(s) occurred in the last {lookback_days} days. Review the causes and add a test or checklist before repeating the workflow.",
                                  "evidence_count": len(failures)})
            if feedback:
                proposals.append({"id": "review-feedback-" + current.strftime("%Y%m%d"),
                                  "kind": "review", "status": "pending_review",
                                  "title": "Review user feedback",
                                  "detail": f"{len(feedback)} feedback note(s) were explicitly recorded. Confirm which preferences should become durable settings; none are changed automatically.",
                                  "evidence_count": len(feedback)})
            if lessons:
                proposals.append({"id": "review-lessons-" + current.strftime("%Y%m%d"),
                                  "kind": "review", "status": "pending_review",
                                  "title": "Consolidate recorded lessons",
                                  "detail": f"{len(lessons)} lesson/preference note(s) are available for manual consolidation into memory. No memory is rewritten automatically.",
                                  "evidence_count": len(lessons)})
            for word, count in repeated[:3]:
                proposals.append({"id": f"pattern-{current.strftime('%Y%m%d')}-{word}",
                                  "kind": "pattern", "status": "pending_review",
                                  "title": f"Review repeated topic: {word}",
                                  "detail": f"The word '{word}' appeared in {count} distinct recent observation word occurrences. This is a weak lexical signal, not proof of a user preference or a learned fact.",
                                  "evidence_count": count})
            report = {"schema_version": 1, "generated_at": current.isoformat(),
                      "lookback_days": lookback_days, "observations_reviewed": len(recent),
                      "summary": {"failures": len(failures), "feedback": len(feedback),
                                  "lessons_and_preferences": len(lessons)},
                      "proposals": proposals[:self.MAX_PROPOSALS],
                      "safety": {"local_only": True, "external_network_used": False,
                                 "code_or_config_modified": False, "actions_executed": False,
                                 "requires_human_review": True}}
            self._atomic_write(self.report_path, report)
            state = {"last_cycle_at": current.isoformat(), "last_cycle_date_utc": current.date().isoformat()}
            self._atomic_write(self.state_path, state)
            return report

    def report(self) -> dict | None:
        """Read the latest report without changing it or creating a new cycle."""
        with self._lock:
            try:
                report = json.loads(self.report_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Evolution report is unreadable; no data was overwritten.") from exc
            if not isinstance(report, dict) or not isinstance(report.get("proposals"), list):
                raise RuntimeError("Evolution report has an invalid structure; no data was overwritten.")
            if len(report["proposals"]) > self.MAX_PROPOSALS:
                raise RuntimeError("Evolution report exceeds configured limits; no data was overwritten.")
            return report

    def status(self) -> dict:
        with self._lock:
            observations = self._read_list(self.observations_path, self.MAX_OBSERVATIONS)
            try:
                report = json.loads(self.report_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                report = None
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Evolution report is unreadable; no data was overwritten.") from exc
            return {"observations": len(observations), "last_report_at": report.get("generated_at") if isinstance(report, dict) else None,
                    "pending_proposals": sum(1 for p in report.get("proposals", []) if p.get("status") == "pending_review") if isinstance(report, dict) else 0,
                    "report_path": str(self.report_path)}

    def review(self, proposal_id: str, decision: str) -> dict:
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ValueError("proposal_id is required.")
        if decision not in {"accepted_for_manual_followup", "rejected"}:
            raise ValueError("decision must be accepted_for_manual_followup or rejected.")
        with self._lock:
            try:
                report = json.loads(self.report_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise ValueError("No evolution report exists yet.") from None
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Evolution report is unreadable; no data was overwritten.") from exc
            found = False
            for proposal in report.get("proposals", []):
                if proposal.get("id") == proposal_id:
                    if proposal.get("status") != "pending_review":
                        raise ValueError("This proposal has already been reviewed.")
                    proposal["status"] = decision
                    found = True
                    break
            if not found:
                raise ValueError("Proposal ID was not found in the latest report.")
            self._atomic_write(self.report_path, report)
            return {"id": proposal_id, "status": decision,
                    "note": "Review records a decision only; it does not edit files, settings, or execute actions."}


def _seconds_until_next_run(hour: int) -> float:
    now = datetime.now().astimezone()
    next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return max(1.0, (next_run - now).total_seconds())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Yuki's opt-in local evolution cycle")
    parser.add_argument("--workspace", default="./yuki_workspace")
    parser.add_argument("--once", action="store_true", help="run one local review cycle now")
    parser.add_argument("--daily", action="store_true", help="opt into a local daily scheduler at the selected local hour")
    parser.add_argument("--hour", type=int, default=3, help="local hour for --daily (0-23; default 03:00)")
    args = parser.parse_args(argv)
    if not (args.once or args.daily):
        parser.error("Choose --once or --daily; no background scheduler starts by default.")
    if not 0 <= args.hour <= 23:
        parser.error("--hour must be between 0 and 23.")
    cycle = DailyEvolutionCycle(args.workspace)
    if args.once:
        report = cycle.run_cycle()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"Yuki daily evolution is enabled locally at {args.hour:02d}:00. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(_seconds_until_next_run(args.hour))
            now = datetime.now().astimezone()
            state = cycle.status()
            # Idempotence: at most one cycle per local calendar date.
            last = state.get("last_report_at")
            if last:
                try:
                    if datetime.fromisoformat(last).astimezone().date() == now.date():
                        continue
                except ValueError:
                    pass
            report = cycle.run_cycle()
            print(f"[{report['generated_at']}] reviewed {report['observations_reviewed']} observations; {len(report['proposals'])} proposals await review.")
    except KeyboardInterrupt:
        print("Yuki daily evolution stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
