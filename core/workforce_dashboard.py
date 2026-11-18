"""Read-only executive dashboard projections over persisted Yuki workforce data.

The service aggregates registry metadata and durable task records. It does not
mutate employee profiles, task state, permissions, or execution policy.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from core.durable_task_engine import DurableTaskEngine
from core.workforce import AgentRegistry


class WorkforceDashboard:
    """Build bounded, read-only workforce and department dashboard snapshots."""

    def __init__(self, *, registry: AgentRegistry, task_engine: DurableTaskEngine):
        if not isinstance(registry, AgentRegistry):
            raise TypeError("registry must be an AgentRegistry")
        if not isinstance(task_engine, DurableTaskEngine):
            raise TypeError("task_engine must be a DurableTaskEngine")
        self.registry = registry
        self.task_engine = task_engine

    def snapshot(self, *, max_goals: int = 100) -> dict[str, Any]:
        """Return live workforce metrics derived from current registry and queue state."""
        if type(max_goals) is not int or not 1 <= max_goals <= 500:
            raise ValueError("max_goals must be an integer between 1 and 500")

        employees = self.registry.list_employees()
        # Fetch one extra row so an exact page is not incorrectly marked truncated.
        goals = self.task_engine.list_goals(limit=max_goals + 1)
        truncated_goals = len(goals) > max_goals
        goals = goals[:max_goals]
        tasks = [
            task
            for goal in goals
            for task in self.task_engine.list_tasks(goal["id"])
            if task["task_type"] == "workforce.task"
        ]

        task_counts = Counter(task["state"] for task in tasks)
        employee_tasks: dict[str, Counter] = defaultdict(Counter)
        for task in tasks:
            assigned = task.get("payload", {}).get("assigned_employee_id")
            if not isinstance(assigned, str):
                continue
            employee_tasks[assigned][task["state"]] += 1
            if task["state"] == "succeeded":
                payload = task.get("payload")
                result = task.get("result")
                # Count only internally consistent task/result envelopes. This is
                # evidence accounting, not cryptographic attestation of a reviewer.
                if (
                    isinstance(payload, dict)
                    and isinstance(result, dict)
                    and result.get("ok") is True
                    and result.get("employee_id") == assigned
                    and payload.get("assigned_employee_id") == assigned
                ):
                    if (
                        payload.get("review_required") is False
                        and result.get("review_required") is False
                        and result.get("review_evidence") is None
                    ):
                        employee_tasks[assigned]["successful_without_review"] += 1
                    elif (
                        payload.get("review_required") is True
                        and result.get("review_required") is True
                        and isinstance(result.get("status"), str)
                        and result["status"].endswith(":reviewed")
                    ):
                        evidence = result.get("review_evidence")
                        if (
                            isinstance(evidence, dict)
                            and set(evidence) == {"reviewer_id", "status", "approved"}
                            and evidence.get("approved") is True
                            and isinstance(evidence.get("reviewer_id"), str)
                            and evidence["reviewer_id"].strip()
                            and evidence["reviewer_id"] != assigned
                            and isinstance(evidence.get("status"), str)
                            and evidence["status"].strip()
                        ):
                            employee_tasks[assigned]["independently_verified"] += 1

        department_rows: dict[str, dict[str, Any]] = {}
        employee_rows = []
        for employee in employees:
            counts = employee_tasks[employee.employee_id]
            active = counts["queued"] + counts["running"]
            employee_rows.append({
                "employee_id": employee.employee_id,
                "name": employee.name,
                "role": employee.role,
                "department": employee.department,
                "reports_to": employee.reports_to,
                "status": employee.status,
                "autonomy_tier": employee.autonomy_tier,
                "capabilities": sorted(employee.capabilities),
                "active_tasks": active,
                "queued_tasks": counts["queued"],
                "running_tasks": counts["running"],
                "succeeded_tasks": counts["succeeded"],
                "failed_tasks": counts["failed"],
                "successful_without_independent_review": counts["successful_without_review"],
                "independently_verified_successes": counts["independently_verified"],
                "capacity": employee.max_concurrent_tasks,
                "available_slots": max(0, employee.max_concurrent_tasks - active),
            })
            department = department_rows.setdefault(employee.department, {
                "name": employee.department,
                "employees": 0,
                "available": 0,
                "busy": 0,
                "offline": 0,
                "disabled": 0,
                "active_tasks": 0,
                "queued_tasks": 0,
                "running_tasks": 0,
                "succeeded_tasks": 0,
                "failed_tasks": 0,
                "successful_without_independent_review": 0,
                "independently_verified_successes": 0,
            })
            department["employees"] += 1
            department[employee.status] += 1
            for state in ("queued", "running", "succeeded", "failed"):
                department[f"{state}_tasks"] += counts[state]
            department["successful_without_independent_review"] += counts["successful_without_review"]
            department["independently_verified_successes"] += counts["independently_verified"]
            department["active_tasks"] += active

        # CEO decision intelligence is descriptive and bounded to the same
        # goal window as this snapshot. It never changes staffing or task state.
        capability_demand: Counter = Counter()
        for task in tasks:
            if task["state"] not in {"queued", "running"}:
                continue
            payload = task.get("payload")
            if not isinstance(payload, dict):
                continue
            required = payload.get("required_capabilities", ())
            if isinstance(required, (list, tuple)):
                for capability in set(
                    item for item in required
                    if isinstance(item, str) and item.strip()
                ):
                    capability_demand[capability] += 1

        capability_supply: Counter = Counter()
        for employee in employee_rows:
            if employee["status"] not in {"available", "busy"} or employee["available_slots"] < 1:
                continue
            for capability in employee["capabilities"]:
                capability_supply[capability] += 1

        capability_signals = [
            {
                "capability": capability,
                "queued_or_running_tasks": demand,
                "employees_with_capacity": capability_supply[capability],
                "coverage": (
                    "no_available_capacity"
                    if capability_supply[capability] == 0
                    else "capacity_present"
                ),
            }
            for capability, demand in sorted(capability_demand.items())
        ]

        department_signals = []
        for department in sorted(department_rows.values(), key=lambda row: row["name"]):
            terminal = department["succeeded_tasks"] + department["failed_tasks"]
            failed = department["failed_tasks"]
            department_signals.append({
                "department": department["name"],
                "queued_tasks": department["queued_tasks"],
                "running_tasks": department["running_tasks"],
                "failed_tasks": failed,
                "failure_rate": (failed / terminal) if terminal else None,
                "failure_rate_denominator": terminal,
                "signal": (
                    "backlog_present" if department["queued_tasks"] > 0
                    else "failures_present" if failed > 0
                    else "no_current_signal"
                ),
            })

        improvement_recommendations = []
        for signal in capability_signals:
            if signal["coverage"] == "no_available_capacity":
                improvement_recommendations.append({
                    "id": f"capability-coverage:{signal['capability']}",
                    "category": "capability_coverage",
                    "priority": "high",
                    "evidence": {
                        "capability": signal["capability"],
                        "queued_or_running_tasks": signal["queued_or_running_tasks"],
                        "employees_with_capacity": signal["employees_with_capacity"],
                    },
                    "recommendation": "Review workforce coverage and consider an approved capability-development or staffing plan.",
                    "requires_human_approval": True,
                    "automatic_action_taken": False,
                })

        for signal in department_signals:
            if signal["queued_tasks"] > 0:
                improvement_recommendations.append({
                    "id": f"department-backlog:{signal['department']}",
                    "category": "department_backlog",
                    "priority": "medium",
                    "evidence": {
                        "department": signal["department"],
                        "queued_tasks": signal["queued_tasks"],
                        "running_tasks": signal["running_tasks"],
                    },
                    "recommendation": "Review task prioritization and available departmental capacity before changing assignments.",
                    "requires_human_approval": True,
                    "automatic_action_taken": False,
                })
            if signal["failed_tasks"] >= 2:
                improvement_recommendations.append({
                    "id": f"failure-review:{signal['department']}",
                    "category": "failure_pattern_review",
                    "priority": "medium",
                    "evidence": {
                        "department": signal["department"],
                        "failed_tasks": signal["failed_tasks"],
                        "failure_rate": signal["failure_rate"],
                        "failure_rate_denominator": signal["failure_rate_denominator"],
                    },
                    "recommendation": "Investigate the failed task records for a shared cause; do not infer employee fault from department-level counts.",
                    "requires_human_approval": True,
                    "automatic_action_taken": False,
                })

        goal_rows = []
        for goal in goals:
            goal_tasks = [task for task in tasks if task["goal_id"] == goal["id"]]
            states = Counter(task["state"] for task in goal_tasks)
            goal_rows.append({
                "goal_id": goal["id"],
                "state": goal["state"],
                "created_at": goal["created_at"],
                "updated_at": goal["updated_at"],
                "total_tasks": len(goal_tasks),
                "task_counts": dict(states),
            })

        return {
            "workforce": {
                "total_employees": len(employees),
                "departments": len(department_rows),
                "available": sum(e.status == "available" for e in employees),
                "busy": sum(e.status == "busy" for e in employees),
                "offline": sum(e.status == "offline" for e in employees),
                "disabled": sum(e.status == "disabled" for e in employees),
                "active_tasks": sum(row["active_tasks"] for row in employee_rows),
            },
            "tasks": {
                "total": len(tasks),
                "queued": task_counts["queued"],
                "running": task_counts["running"],
                "succeeded": task_counts["succeeded"],
                "failed": task_counts["failed"],
                "cancelled": task_counts["cancelled"],
            },
            "performance": {
                "scope": "workforce tasks in the dashboard's bounded goal window",
                "independently_verified_successes": sum(row["independently_verified_successes"] for row in employee_rows),
                "successful_without_independent_review": sum(row["successful_without_independent_review"] for row in employee_rows),
                "failed_tasks": sum(row["failed_tasks"] for row in employee_rows),
                "scoring": "descriptive evidence counts only; no quality score or ranking is inferred",
            },
            "departments": sorted(department_rows.values(), key=lambda row: row["name"]),
            "decision_intelligence": {
                "scope": "descriptive signals from the dashboard's bounded goal window and current registry",
                "capability_demand": capability_signals,
                "department_signals": department_signals,
                "improvement_recommendations": improvement_recommendations,
                "limitations": [
                    "recommendations are deterministic review prompts, not autonomous plans or completed actions",
                    "capability gaps do not establish that hiring or reassignment is the only remedy",
                    "failure-pattern prompts require at least two failed tasks in a department and do not assign blame",
                    "capacity counts are current registry metadata, not guaranteed future availability",
                    "failure rates exclude cancelled tasks and are omitted when no succeeded/failed tasks exist",
                    "signals are not forecasts, employee quality scores, or authorization to reassign work",
                ],
            },
            "employees": employee_rows,
            "goals": goal_rows,
            "truncated_goals": truncated_goals,
        }


    def employee_directory(
        self,
        *,
        department: str | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """Return a validated, paginated employee directory for dashboard views."""
        if type(page) is not int or page < 1:
            raise ValueError("page must be a positive integer")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if department is not None and (not isinstance(department, str) or not department.strip()):
            raise ValueError("department must be non-empty text when provided")
        allowed_statuses = {"available", "busy", "offline", "disabled"}
        if status is not None and (
            not isinstance(status, str) or status not in allowed_statuses
        ):
            raise ValueError("unsupported employee status")
        if search is not None and (not isinstance(search, str) or len(search) > 120):
            raise ValueError("search must be text up to 120 characters")

        employees = self.snapshot()["employees"]
        needle = search.strip().casefold() if search else ""
        filtered = [
            employee for employee in employees
            if (department is None or employee["department"].casefold() == department.casefold())
            and (status is None or employee["status"] == status)
            and (
                not needle
                or needle in employee["employee_id"].casefold()
                or needle in employee["name"].casefold()
                or needle in employee["role"].casefold()
                or needle in employee["department"].casefold()
                or any(needle in capability.casefold() for capability in employee["capabilities"])
            )
        ]
        total = len(filtered)
        start = (page - 1) * page_size
        return {
            "employees": filtered[start:start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    def department_detail(self, department: str) -> dict[str, Any]:
        """Return one department's live summary and employee roster."""
        if not isinstance(department, str) or not department.strip():
            raise ValueError("department must be non-empty text")
        snapshot = self.snapshot()
        summary = next(
            (row for row in snapshot["departments"]
             if row["name"].casefold() == department.casefold()),
            None,
        )
        if summary is None:
            raise KeyError(f"unknown department: {department}")
        roster = [
            employee for employee in snapshot["employees"]
            if employee["department"] == summary["name"]
        ]
        return {"department": summary, "employees": roster}

    def goal_detail(self, goal_id: str, *, after_event_id: int = 0, event_limit: int = 100) -> dict[str, Any]:
        """Return a bounded, read-only execution timeline for one persisted goal."""
        if not isinstance(goal_id, str) or not 1 <= len(goal_id) <= 256:
            raise ValueError("goal_id must be non-empty bounded text")
        if type(after_event_id) is not int or after_event_id < 0:
            raise ValueError("after_event_id must be a non-negative integer")
        if type(event_limit) is not int or not 1 <= event_limit <= 200:
            raise ValueError("event_limit must be between 1 and 200")
        goal = self.task_engine.get_goal(goal_id)
        employee_by_id = {employee.employee_id: employee for employee in self.registry.list_employees()}
        task_rows = []
        for task in self.task_engine.list_tasks(goal_id):
            if task["task_type"] != "workforce.task":
                continue
            payload = task.get("payload") or {}
            employee_id = payload.get("assigned_employee_id")
            employee = employee_by_id.get(employee_id) if isinstance(employee_id, str) else None
            description = payload.get("description")
            task_rows.append({
                "task_id": task["id"],
                "description": description[:2000] if isinstance(description, str) else "",
                "employee_id": employee_id if isinstance(employee_id, str) else None,
                "employee_name": employee.name if employee else None,
                "department": employee.department if employee else None,
                "state": task["state"],
                "attempts": task["attempts"],
                "max_attempts": task["max_attempts"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
            })
        events = self.task_engine.list_events(goal_id, after_id=after_event_id, limit=event_limit + 1)
        has_more = len(events) > event_limit
        events = events[:event_limit]
        # Omit arbitrary event detail; executor messages may contain sensitive data.
        event_rows = [{
            "id": event["id"],
            "task_id": event["task_id"],
            "event_type": event["event_type"],
            "created_at": event["created_at"],
        } for event in events]
        return {
            "goal": {
                "goal_id": goal["id"],
                "state": goal["state"],
                "created_at": goal["created_at"],
                "updated_at": goal["updated_at"],
            },
            "tasks": task_rows,
            "events": event_rows,
            "next_event_id": events[-1]["id"] if events else after_event_id,
            "has_more_events": has_more,
        }
