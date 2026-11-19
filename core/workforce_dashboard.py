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
        # Validate lesson references against the bounded, current verified registry.
        # This cannot prove the lesson was verified at the historical execution time.
        verified_knowledge = self.task_engine.list_verified_knowledge(limit=500)
        currently_verified_knowledge_ids = {
            record.get("knowledge_id")
            for record in verified_knowledge
            if isinstance(record, dict)
            and isinstance(record.get("knowledge_id"), str)
            and record.get("state") == "verified"
        }
        knowledge_outcomes: dict[str, dict[str, Any]] = {}
        for task in tasks:
            payload = task.get("payload")
            context = payload.get("knowledge_context", []) if isinstance(payload, dict) else []
            if not isinstance(context, list):
                continue
            seen_knowledge_ids: set[str] = set()
            for lesson in context[:5]:
                if not isinstance(lesson, dict) or not isinstance(lesson.get("knowledge_id"), str):
                    continue
                knowledge_id = lesson["knowledge_id"].strip()
                if not knowledge_id or len(knowledge_id) > 128 or knowledge_id in seen_knowledge_ids:
                    continue
                # One task contributes at most one exposure per lesson, even if
                # malformed/replayed context repeats the same knowledge reference.
                seen_knowledge_ids.add(knowledge_id)
                metrics = knowledge_outcomes.setdefault(knowledge_id, {
                    "knowledge_id": knowledge_id,
                    "exposures": 0,
                    "succeeded_tasks": 0,
                    "failed_tasks": 0,
                    "independently_verified_successes": 0,
                    "currently_verified_exposures": 0,
                    "currently_verified_succeeded_tasks": 0,
                    "currently_verified_failed_tasks": 0,
                    "unverified_reference_exposures": 0,
                })
                metrics["exposures"] += 1
                is_currently_verified = knowledge_id in currently_verified_knowledge_ids
                if is_currently_verified:
                    metrics["currently_verified_exposures"] += 1
                else:
                    metrics["unverified_reference_exposures"] += 1
                if task["state"] == "succeeded":
                    metrics["succeeded_tasks"] += 1
                    if is_currently_verified:
                        metrics["currently_verified_succeeded_tasks"] += 1
                    result = task.get("result")
                    evidence = result.get("review_evidence") if isinstance(result, dict) else None
                    if (
                        isinstance(payload, dict)
                        and payload.get("review_required") is True
                        and isinstance(result, dict)
                        and result.get("ok") is True
                        and result.get("review_required") is True
                        and isinstance(result.get("status"), str)
                        and result["status"].endswith(":reviewed")
                        and isinstance(evidence, dict)
                        and set(evidence) == {"reviewer_id", "status", "approved"}
                        and evidence.get("approved") is True
                        and isinstance(payload.get("assigned_employee_id"), str)
                        and payload["assigned_employee_id"].strip()
                        and result.get("employee_id") == payload["assigned_employee_id"]
                        and isinstance(evidence.get("reviewer_id"), str)
                        and evidence["reviewer_id"].strip()
                        and evidence["reviewer_id"].strip() != payload["assigned_employee_id"].strip()
                        and isinstance(evidence.get("status"), str)
                        and evidence["status"].strip().casefold() == "approved"
                    ):
                        metrics["independently_verified_successes"] += 1
                elif task["state"] == "failed":
                    metrics["failed_tasks"] += 1
                    if is_currently_verified:
                        metrics["currently_verified_failed_tasks"] += 1

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
                            and evidence["reviewer_id"].strip() != assigned.strip()
                            and isinstance(evidence.get("status"), str)
                            and evidence["status"].strip().casefold() == "approved"
                        ):
                            employee_tasks[assigned]["independently_verified"] += 1

        # Group recurring failures by stable operational dimensions only.
        # Do not expose employee-level blame labels or free-form failure details.
        employee_by_id = {employee.employee_id: employee for employee in employees}
        failure_groups: dict[tuple[str, str, tuple[str, ...]], Counter] = defaultdict(Counter)
        for task in tasks:
            if task["state"] not in {"succeeded", "failed"}:
                continue
            payload = task.get("payload")
            if not isinstance(payload, dict):
                continue
            assigned_id = payload.get("assigned_employee_id")
            employee = employee_by_id.get(assigned_id) if isinstance(assigned_id, str) else None
            department = employee.department if employee else "unassigned"
            required = payload.get("required_capabilities", ())
            capabilities = tuple(sorted({
                item.strip() for item in required
                if isinstance(item, str) and item.strip() and len(item) <= 80
            })) if isinstance(required, (list, tuple)) else ()
            group = failure_groups[(department, task["task_type"], capabilities)]
            group["completed_tasks"] += 1
            group[task["state"]] += 1

        # Only the durable store's verified records are eligible for coverage.
        # Capability matching is deliberately exact against curated knowledge tags;
        # free-text lesson/domain similarity would create unsupported associations.
        knowledge_by_capability: dict[str, list[str]] = defaultdict(list)
        for record in verified_knowledge:
            knowledge = record.get("knowledge") if isinstance(record, dict) else None
            if not isinstance(knowledge, dict) or not isinstance(knowledge.get("tags"), list):
                continue
            for tag in knowledge["tags"]:
                if isinstance(tag, str) and tag.strip():
                    knowledge_by_capability[tag.strip().casefold()].append(record["knowledge_id"])

        failure_patterns = []
        for (department, task_type, capabilities), counts in sorted(failure_groups.items()):
            failed = counts["failed"]
            completed = counts["completed_tasks"]
            if failed < 2 or completed < 3:
                continue
            matched_knowledge = sorted({
                knowledge_id
                for capability in capabilities
                for knowledge_id in knowledge_by_capability.get(capability.casefold(), [])
            })
            failure_patterns.append({
                "department": department,
                "task_type": task_type,
                "required_capabilities": list(capabilities),
                "failed_tasks": failed,
                "succeeded_tasks": counts["succeeded"],
                "completed_tasks": completed,
                "failure_rate": failed / completed,
                "verified_knowledge_ids": matched_knowledge,
                "knowledge_coverage": (
                    "verified_tag_match_available" if matched_knowledge
                    else "no_verified_tag_match"
                ),
                "signal_strength": "repeated_observation",
                "evidence_scope": "bounded_dashboard_goal_window",
                "employee_attribution": False,
                "raw_failure_details_included": False,
            })

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

        capability_failures: Counter = Counter()
        capability_terminal: Counter = Counter()
        for (_, _, capabilities), counts in failure_groups.items():
            for capability in capabilities:
                capability_failures[capability] += counts["failed"]
                capability_terminal[capability] += counts["completed_tasks"]

        capability_gap_signals = []
        capability_names = sorted(set(capability_demand) | set(capability_failures))
        for capability in capability_names:
            demand = capability_demand[capability]
            capacity = capability_supply[capability]
            failed = capability_failures[capability]
            terminal = capability_terminal[capability]
            matching_lessons = sorted(knowledge_by_capability.get(capability.casefold(), []))
            if demand > 0 and capacity == 0:
                assessment = "backlog_without_current_capacity"
            elif failed >= 2 and terminal >= 3:
                assessment = "repeated_failures_with_current_capacity" if capacity else "repeated_failures_without_current_capacity"
            elif demand > 0:
                assessment = "backlog_with_current_capacity"
            elif failed > 0:
                assessment = "limited_failure_observations"
            else:
                assessment = "no_current_gap_signal"
            capability_gap_signals.append({
                "capability": capability,
                "queued_or_running_tasks": demand,
                "employees_with_capacity": capacity,
                "failed_tasks": failed,
                "terminal_tasks": terminal,
                "failure_rate": failed / terminal if terminal else None,
                "verified_knowledge_ids": matching_lessons,
                "knowledge_coverage": "verified_tag_match_available" if matching_lessons else "no_verified_tag_match",
                "assessment": assessment,
                "evidence_scope": "bounded_dashboard_goal_window_and_current_registry",
                "signal_strength": (
                    "repeated_observation" if failed >= 2 and terminal >= 3
                    else "limited_observation" if failed else "current_capacity_snapshot"
                ),
            })

        # Convert repeated capability failures into a bounded development-review
        # plan. A verified tag match is only a candidate resource; outcome exposure
        # counts are descriptive and never treated as proof of lesson effectiveness.
        skill_development_plans = []
        for signal in capability_gap_signals:
            if signal["failed_tasks"] < 2 or signal["terminal_tasks"] < 3:
                continue
            lesson_evidence = []
            for knowledge_id in signal["verified_knowledge_ids"]:
                outcome = knowledge_outcomes.get(knowledge_id, {})
                exposures = outcome.get("currently_verified_exposures", 0)
                verified_successes = outcome.get("independently_verified_successes", 0)
                if exposures >= 3 and verified_successes >= 1:
                    evidence_level = "reviewed_success_observation_available"
                elif exposures > 0:
                    evidence_level = "limited_outcome_observations"
                else:
                    evidence_level = "no_outcome_exposure_in_window"
                lesson_evidence.append({
                    "knowledge_id": knowledge_id,
                    "exposures": exposures,
                    "succeeded_tasks": outcome.get("currently_verified_succeeded_tasks", 0),
                    "failed_tasks": outcome.get("currently_verified_failed_tasks", 0),
                    "independently_verified_successes": verified_successes if knowledge_id in currently_verified_knowledge_ids else 0,
                    "unverified_reference_exposures": outcome.get("unverified_reference_exposures", 0),
                    "provenance": "currently_verified_record_match" if knowledge_id in currently_verified_knowledge_ids else "not_found_in_bounded_verified_registry",
                    "evidence_level": evidence_level,
                })
            if not lesson_evidence:
                evidence_level = "no_exact_verified_lesson_match"
            elif any(item["evidence_level"] == "reviewed_success_observation_available" for item in lesson_evidence):
                evidence_level = "reviewed_success_observation_available"
            elif any(item["evidence_level"] == "limited_outcome_observations" for item in lesson_evidence):
                evidence_level = "limited_outcome_observations"
            else:
                evidence_level = "no_outcome_exposure_in_window"
            skill_development_plans.append({
                "capability": signal["capability"],
                "failed_tasks": signal["failed_tasks"],
                "terminal_tasks": signal["terminal_tasks"],
                "failure_rate": signal["failure_rate"],
                "verified_lessons": lesson_evidence,
                "evidence_level": evidence_level,
                "evidence_scope": "bounded_dashboard_goal_window_and_newest_500_verified_knowledge_records",
                "recommended_review": (
                    "Have a qualified reviewer assess representative failures and determine whether the matched verified lessons are applicable; if not, consider a human-authored development or knowledge proposal."
                    if lesson_evidence
                    else "Have a qualified reviewer inspect representative failures and consider a human-authored skill-development or knowledge proposal; do not infer or fabricate a lesson."
                ),
                "requires_human_approval": True,
                "automatic_training_or_assignment_change": False,
            })

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

        for signal in capability_gap_signals:
            if signal["assessment"] not in {
                "backlog_without_current_capacity",
                "repeated_failures_with_current_capacity",
                "repeated_failures_without_current_capacity",
            }:
                continue
            high_priority = signal["assessment"] == "backlog_without_current_capacity"
            improvement_recommendations.append({
                "id": f"capability-gap:{signal['capability']}",
                "category": "capability_gap_review",
                "priority": "high" if high_priority else "medium",
                "evidence": {
                    "capability": signal["capability"],
                    "assessment": signal["assessment"],
                    "queued_or_running_tasks": signal["queued_or_running_tasks"],
                    "employees_with_capacity": signal["employees_with_capacity"],
                    "failed_tasks": signal["failed_tasks"],
                    "terminal_tasks": signal["terminal_tasks"],
                    "failure_rate": signal["failure_rate"],
                    "verified_knowledge_ids": signal["verified_knowledge_ids"],
                    "knowledge_coverage": signal["knowledge_coverage"],
                    "evidence_scope": signal["evidence_scope"],
                },
                "recommendation": (
                    "Review scheduling, task prioritization, and approved capability development; current registry capacity is a snapshot, not proof that hiring or reassignment is required."
                    if high_priority
                    else "Review representative failed tasks, available capacity, and matching verified lessons before deciding whether workflow, training, or staffing changes are warranted."
                ),
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

        for lesson in knowledge_outcomes.values():
            if lesson["exposures"] >= 3 and lesson["failed_tasks"] >= 2:
                improvement_recommendations.append({
                    "id": f"knowledge-review:{lesson['knowledge_id']}",
                    "category": "knowledge_applicability_review",
                    "priority": "medium",
                    "evidence": {
                        "knowledge_id": lesson["knowledge_id"],
                        "exposures": lesson["exposures"],
                        "succeeded_tasks": lesson["succeeded_tasks"],
                        "failed_tasks": lesson["failed_tasks"],
                        "independently_verified_successes": lesson["independently_verified_successes"],
                    },
                    "recommendation": "Review whether this lesson is still applicable to the task contexts where it was used; inspect individual task evidence before proposing a revision or retirement.",
                    "requires_human_approval": True,
                    "automatic_action_taken": False,
                })

        for plan in skill_development_plans:
            improvement_recommendations.append({
                "id": f"skill-development:{plan['capability']}",
                "category": "skill_development_review",
                "priority": "medium",
                "evidence": {
                    "capability": plan["capability"],
                    "failed_tasks": plan["failed_tasks"],
                    "terminal_tasks": plan["terminal_tasks"],
                    "failure_rate": plan["failure_rate"],
                    "evidence_level": plan["evidence_level"],
                    "verified_knowledge_ids": [item["knowledge_id"] for item in plan["verified_lessons"]],
                },
                "recommendation": plan["recommended_review"],
                "requires_human_approval": True,
                "automatic_action_taken": False,
            })

        for index, pattern in enumerate(failure_patterns):
            improvement_recommendations.append({
                "id": f"failure-pattern:{index}",
                "category": "recurring_failure_pattern",
                "priority": "medium" if pattern["failure_rate"] < 0.5 else "high",
                "evidence": {
                    "department": pattern["department"],
                    "task_type": pattern["task_type"],
                    "required_capabilities": pattern["required_capabilities"],
                    "failed_tasks": pattern["failed_tasks"],
                    "succeeded_tasks": pattern["succeeded_tasks"],
                    "completed_tasks": pattern["completed_tasks"],
                    "failure_rate": pattern["failure_rate"],
                    "evidence_scope": pattern["evidence_scope"],
                    "verified_knowledge_ids": pattern["verified_knowledge_ids"],
                    "knowledge_coverage": pattern["knowledge_coverage"],
                },
                "recommendation": (
                    "Review matching verified lessons and representative task evidence; a tag match does not establish lesson applicability or effectiveness."
                    if pattern["verified_knowledge_ids"]
                    else "No verified lesson has an exact capability-tag match; review whether curated knowledge is needed alongside representative task evidence."
                ),
                "requires_human_approval": True,
                "automatic_action_taken": False,
            })

        goal_rows = []
        for goal in goals:
            goal_tasks = [task for task in tasks if task["goal_id"] == goal["id"]]
            states = Counter(task["state"] for task in goal_tasks)
            terminal_states = {"succeeded", "failed", "cancelled"}
            if not goal_tasks:
                task_outcome = "no_tasks"
            elif any(task["state"] not in terminal_states for task in goal_tasks):
                task_outcome = "in_progress"
            elif states["succeeded"] == len(goal_tasks):
                task_outcome = "succeeded"
            elif states["succeeded"] > 0:
                task_outcome = "partially_succeeded"
            elif states["failed"] > 0:
                task_outcome = "failed"
            else:
                task_outcome = "cancelled"
            goal_rows.append({
                "goal_id": goal["id"],
                "state": goal["state"],
                "task_outcome": task_outcome,
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
            "knowledge_effectiveness": {
                "scope": "descriptive outcomes for tasks carrying each verified lesson in the bounded dashboard goal window",
                "lessons": sorted(knowledge_outcomes.values(), key=lambda row: row["knowledge_id"]),
                "limitations": [
                    "exposure counts show association, not that a lesson caused an outcome",
                    "tasks may use multiple lessons and differ in difficulty, staffing, and context",
                    "raw exposure counts include caller-provided lesson references; currently verified registry matches are reported separately",
                    "current verified status does not prove a lesson was verified at the historical task execution time",
                    "verified-registry lookup is bounded to the newest 500 records; missing references are not proof that a lesson never existed",
                    "only independently reviewed successes with consistent persisted evidence count as verified successes",
                    "this is observational reporting, not an automatic quality score or lesson retirement decision",
                ],
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
                "capability_gap_signals": capability_gap_signals,
                "skill_development_plans": skill_development_plans,
                "department_signals": department_signals,
                "failure_patterns": failure_patterns,
                "improvement_recommendations": improvement_recommendations,
                "limitations": [
                    "recommendations are deterministic review prompts, not autonomous plans or completed actions",
                    "capability gaps do not establish that hiring or reassignment is the only remedy",
                    "failure-pattern prompts require at least two failed tasks in a department and do not assign blame",
                    "capacity counts are current registry metadata, not guaranteed future availability",
                    "failure rates exclude cancelled tasks and are omitted when no succeeded/failed tasks exist",
                    "recurring failure patterns require at least two failures and three terminal observations in a department/task-type/capability group",
                    "failure patterns are descriptive, do not attribute fault to individual employees, and omit raw failure details",
                    "knowledge coverage uses exact verified-lesson tag matches only; a match does not prove applicability or effectiveness, and no match does not prove knowledge is absent",
                    "capability gap assessments combine bounded task observations with a point-in-time registry capacity snapshot; they are review priorities, not forecasts or proof of staffing shortages",
                    "verified knowledge listing is bounded to the newest 500 records; coverage may omit older verified lessons",
                    "signals are not forecasts, employee quality scores, or authorization to reassign work",
                    "skill-development plans are review prompts; lesson exposure and reviewed successes are observational and do not establish that a lesson caused improvement",
                    "skill-development evidence is limited to tasks in the bounded goal window and exact capability-tag matches among the newest 500 verified knowledge records",
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
