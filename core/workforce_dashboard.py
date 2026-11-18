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
        goals = self.task_engine.list_goals(limit=max_goals)
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
            if isinstance(assigned, str):
                employee_tasks[assigned][task["state"]] += 1

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
            })
            department["employees"] += 1
            department[employee.status] += 1
            for state in ("queued", "running", "succeeded", "failed"):
                department[f"{state}_tasks"] += counts[state]
            department["active_tasks"] += active

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
            "departments": sorted(department_rows.values(), key=lambda row: row["name"]),
            "employees": employee_rows,
            "goals": goal_rows,
            "truncated_goals": len(goals) == max_goals,
        }
