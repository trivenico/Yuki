"""Optional Textual supervision UI for status and explicitly safe commands."""
from __future__ import annotations

from uuid import uuid4

from automation.agent_coordinator import SubtaskSpec
from core.planner import Planner
from ui.task_controls import approve_queued_task, execute_worker_cycle
from ui.task_views import render_task_events, render_task_summaries


def run_dashboard(
    agent=None, *, command_handler=None, task_runtime=None, actor_id=None,
    worker_supervisor=None, authorize=None, coordinator=None,
    approval_session_id=None,
) -> None:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Vertical
        from textual.widgets import Footer, Header, Input, Label, RichLog
    except ImportError as exc:
        raise RuntimeError("Install optional Textual with: pip install -r requirements-ui.txt") from exc

    class YukiDashboard(App):
        TITLE = "Yuki — Supervision"
        CSS = "Screen { padding: 1; } #log { height: 1fr; border: round $primary; } #command { dock: bottom; }"
        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                yield Label("Yuki supervision console · sensitive actions remain approval-gated")
                yield RichLog(id="log", wrap=True, markup=False)
                yield Input(placeholder="/plan create GOAL · /plan PLAN_ID · /plan cancel PLAN_ID CANCEL · /approve TASK_ID VERSION APPROVE", id="command")
            yield Footer()
        def on_mount(self):
            self.query_one("#log", RichLog).write("Dashboard ready. No background actions are running.")
        def on_input_submitted(self, event: Input.Submitted):
            command = event.value.strip()
            event.input.value = ""
            log = self.query_one("#log", RichLog)
            if command == "/quit":
                self.exit()
                return
            if command.startswith("/task create "):
                parts = command.split(maxsplit=3)
                if len(parts) != 4 or coordinator is None or actor_id is None:
                    log.write("Usage: /task create ROLE REQUEST (coordinator unavailable or request invalid).")
                    return
                try:
                    request = parts[3].strip()
                    if not request or len(request) > 2000 or "\x00" in request:
                        raise ValueError("request must be bounded non-empty text.")
                    plan = coordinator.submit(
                        actor_id=actor_id,
                        idempotency_key=f"dashboard:{uuid4().hex}",
                        subtasks=[SubtaskSpec(
                            subtask_id=f"task-{uuid4().hex[:12]}",
                            description=request,
                            required_capabilities=(parts[2],),
                            requires_approval=True,
                        )],
                    )
                    plan = coordinator.advance(actor_id=actor_id, plan_id=plan.plan_id)
                    log.write(f"Plan created: {plan.plan_id}")
                    for item in plan.subtasks:
                        log.write(
                            f"{item.agent_id} | {item.status} | "
                            f"task={item.task_id or 'not-dispatched'}"
                        )
                    log.write("Review the task, then approve it with /approve TASK_ID VERSION APPROVE.")
                except Exception:
                    log.write("Task creation failed or the requested specialist is unavailable.")
                return
            if command == "/plan" or command.startswith("/plan "):
                if command.startswith("/plan create "):
                    goal = command[len("/plan create "):].strip()
                    if coordinator is None or actor_id is None or not goal:
                        log.write("Usage: /plan create GOAL (planner unavailable or goal missing).")
                        return
                    try:
                        proposed = Planner(max_steps=10).decompose(goal)
                        plan = coordinator.submit(
                            actor_id=actor_id,
                            idempotency_key=f"planner:{uuid4().hex}",
                            subtasks=[
                                SubtaskSpec(
                                    subtask_id=item.task_id,
                                    description=item.description,
                                    required_capabilities=item.required_capabilities,
                                    depends_on=item.depends_on,
                                    requires_approval=item.requires_approval,
                                )
                                for item in proposed
                            ],
                        )
                        plan = coordinator.advance(actor_id=actor_id, plan_id=plan.plan_id)
                        log.write(f"Proposed plan: {plan.plan_id} · {len(plan.subtasks)} specialist tasks")
                        for item in plan.subtasks:
                            log.write(
                                f"{item.subtask_id} | {item.agent_id} | {item.status} | "
                                f"depends_on={','.join(item.depends_on) or 'none'} | "
                                f"task={item.task_id or 'waiting for dependencies'}"
                            )
                        log.write("Review the DAG. Approve queued tasks individually; nothing runs automatically.")
                    except Exception:
                        log.write("Plan proposal failed or a required specialist is unavailable.")
                    return
                parts = command.split()
                if len(parts) == 2 and coordinator is not None and actor_id is not None:
                    try:
                        plan = coordinator.get_plan(actor_id=actor_id, plan_id=parts[1])
                        log.write(f"Plan {plan.plan_id}")
                        for item in plan.subtasks:
                            log.write(
                                f"{item.subtask_id} | {item.agent_id} | {item.status} | "
                                f"task={item.task_id or 'not-dispatched'} | "
                                f"depends_on={','.join(item.depends_on) or 'none'}"
                            )
                    except Exception:
                        log.write("Plan not found or plan inspection is not authorized.")
                    return
                if len(parts) == 4 and parts[1] == "cancel" and parts[3] == "CANCEL" and coordinator is not None and actor_id is not None:
                    try:
                        plan = coordinator.cancel_plan(actor_id=actor_id, plan_id=parts[2])
                        log.write(f"Cancellation reconciled for plan {plan.plan_id}.")
                        for item in plan.subtasks:
                            log.write(f"{item.subtask_id} | {item.status}")
                    except PermissionError:
                        log.write("Plan cancellation is not authorized.")
                    except Exception:
                        log.write("Plan cancellation failed; inspect current task states before retrying.")
                    return
                log.write("Usage: /plan PLAN_ID | /plan cancel PLAN_ID CANCEL")
                return
            if command.startswith("/approve "):
                parts = command.split()
                if len(parts) != 4 or task_runtime is None or actor_id is None or approval_session_id is None:
                    log.write("Usage: /approve TASK_ID VERSION APPROVE")
                    return
                try:
                    version = int(parts[2])
                    log.write(approve_queued_task(
                        task_runtime, actor_id=actor_id,
                        session_id=approval_session_id, authorize=authorize,
                        task_id=parts[1], expected_version=version,
                        confirmation=parts[3],
                    ))
                except PermissionError:
                    log.write("Task approval is not authorized.")
                except (ValueError, TypeError):
                    log.write("Invalid approval command, task version, or task state.")
                except Exception:
                    log.write("Task approval failed; reload /tasks and verify the task version.")
                return
            if command == "/worker run RUN":
                if worker_supervisor is None or actor_id is None or authorize is None:
                    log.write("Worker execution is not configured.")
                    return
                try:
                    log.write(execute_worker_cycle(
                        worker_supervisor, actor_id=actor_id,
                        authorize=authorize, confirmation="RUN",
                    ))
                except PermissionError:
                    log.write("Worker execution is not authorized.")
                except (TypeError, ValueError):
                    log.write("Worker execution configuration or confirmation is invalid.")
                except Exception:
                    log.write("Worker cycle failed safely; inspect the authorized task event log.")
                return
            if command == "/tasks":
                if task_runtime is None or actor_id is None:
                    log.write("Task monitoring is not configured.")
                    return
                try:
                    log.write(render_task_summaries(task_runtime, actor_id))
                except Exception:
                    log.write("Task listing failed or is not authorized.")
                return
            if command == "/events" or command.startswith("/events "):
                parts = command.split()
                if len(parts) not in {2, 3}:
                    log.write("Usage: /events TASK_ID [AFTER_SEQUENCE]")
                    return
                if task_runtime is None or actor_id is None:
                    log.write("Task monitoring is not configured.")
                    return
                try:
                    cursor = int(parts[2]) if len(parts) == 3 else 0
                    log.write(render_task_events(
                        task_runtime, actor_id, parts[1], after_sequence=cursor,
                    ))
                except (ValueError, TypeError):
                    log.write("Invalid event cursor or task identifier.")
                except Exception:
                    log.write("Event retrieval failed or is not authorized.")
                return
            if command in {"/help", "/status", "/skills"} and command_handler:
                try:
                    log.write(str(command_handler(command))[:12000])
                except Exception:
                    log.write("Command failed safely.")
            elif command == "/status" and agent is not None:
                log.write(f"Registered skills: {', '.join(agent.registry.names())}")
            elif command == "/skills" and agent is not None:
                for skill in agent.available_skills():
                    log.write(f"{skill['name']} [{skill['risk']}]: {skill['description']}")
            else:
                log.write("This dashboard accepts only safe status commands; no action was executed.")
    YukiDashboard().run()
