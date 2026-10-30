"""Optional Textual supervision UI for status and explicitly safe commands."""
from __future__ import annotations

from ui.task_controls import execute_worker_cycle
from ui.task_views import render_task_events, render_task_summaries


def run_dashboard(
    agent=None, *, command_handler=None, task_runtime=None, actor_id=None,
    worker_supervisor=None, authorize=None,
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
                yield Input(placeholder="/tasks · /events TASK_ID [CURSOR] · /worker run RUN · /status · /quit", id="command")
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
