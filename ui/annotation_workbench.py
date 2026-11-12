"""Interactive Textual client for the owner-scoped annotation workbench.

The UI is deliberately a thin client: authorization, validation, optimistic
version checks and persistence remain in SQLiteAnnotationWorkbench. No
marketplace delivery or model-assisted annotation is triggered by this UI.
"""
from __future__ import annotations

import json
import shlex
from typing import Any

from core.data_annotation import AnnotationConflict


def execute_annotation_command(
    workbench: Any, owner_id: str, command: str, *, open_project_id: str = "",
) -> str:
    """Execute one explicit local workbench command and return display-safe text."""
    if not isinstance(owner_id, str) or not owner_id.strip():
        return "Annotation workbench is not configured with an owner identity."
    if not isinstance(command, str) or len(command) > 70_000:
        return "Command is invalid or too large."
    command = command.strip()
    if not command:
        return ""
    if command == "/help":
        return (
            "Commands:\n"
            "/projects — list your projects\n"
            "/open PROJECT_ID — show project instructions and rubric\n"
            "/tasks [pending|in_progress|submitted|accepted|rework] — list tasks in the open project\n"
            "/task TASK_ID — inspect a task and its current version\n"
            "/claim TASK_ID VERSION — claim or reclaim a task using its current version\n"
            "/submit TASK_ID VERSION CONFIRM JSON — submit your human-confirmed annotation\n"
            "/submissions TASK_ID — show submissions for a task\n"
            "/review SUBMISSION_ID accepted|rework [NOTES] — review a submission\n"
            "/quit — exit"
        )
    if command == "/projects":
        projects = workbench.list_projects(owner_id, limit=100)
        if not projects:
            return "No annotation projects yet."
        return "\n".join(
            f"{p.name} | {p.task_type} | {p.status} | id={p.project_id}"
            for p in projects
        )
    try:
        parts = shlex.split(command)
    except ValueError:
        return "Invalid command quoting. Type /help for syntax."
    if not parts:
        return ""
    action = parts[0]
    try:
        if action == "/open" and len(parts) == 2:
            project = workbench.get_project(owner_id, parts[1])
            if project is None:
                return "Project not found."
            return (
                f"{project.name} [{project.task_type}] · {project.status}\n"
                f"AI assistance: {project.ai_assistance_mode}\n"
                f"Instructions:\n{project.instructions}\n"
                f"Rubric:\n{json.dumps(project.rubric, ensure_ascii=False, indent=2)}\n"
                f"Project ID: {project.project_id}"
            )
        if action == "/tasks" and len(parts) in {1, 2}:
            status = parts[1] if len(parts) == 2 else None
            tasks = workbench.list_tasks(owner_id, open_project_id, status=status, limit=200)
            if not tasks:
                return "No matching tasks in the open project."
            return "\n".join(
                f"{t.status} | v{t.version} | {t.external_ref} | id={t.task_id}"
                for t in tasks
            )
        if action == "/task" and len(parts) == 2:
            task = workbench.get_task(owner_id, parts[1])
            if task is None:
                return "Task not found."
            return (
                f"Task {task.task_id}\nProject: {task.project_id}\n"
                f"External reference: {task.external_ref}\n"
                f"Status: {task.status} · version {task.version}\n"
                f"Asset reference: {task.asset_ref or 'none'}\n"
                f"Input:\n{json.dumps(task.input_data, ensure_ascii=False, indent=2)}"
            )
        if action == "/claim" and len(parts) == 3:
            task = workbench.claim_task(
                owner_id, parts[1], expected_version=_positive_version(parts[2]),
            )
            return "Task not found." if task is None else (
                f"Claimed task {task.task_id}; status={task.status}, version={task.version}. "
                "Refresh task details before editing."
            )
        if action == "/submit" and len(parts) >= 5:
            if parts[3] != "CONFIRM":
                return "Submission requires the literal CONFIRM after reviewing your annotation."
            # JSON is parsed from the original command remainder to preserve nested spaces.
            prefix = f"/submit {parts[1]} {parts[2]} CONFIRM "
            raw_payload = command[len(prefix):].strip()
            if len(raw_payload.encode("utf-8")) > 64 * 1024:
                return "Annotation exceeds the 64 KiB payload limit."
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict):
                return "Annotation must be a JSON object."
            submission = workbench.submit(
                owner_id, parts[1], payload=payload,
                expected_version=_positive_version(parts[2]),
                ai_assisted=False, human_confirmed=True,
            )
            return "Task not found." if submission is None else (
                f"Submission recorded: {submission.submission_id} · {submission.status}. "
                "This has not been delivered to a client or marketplace."
            )
        if action == "/submissions" and len(parts) == 2:
            submissions = workbench.list_submissions(owner_id, parts[1])
            if not submissions:
                return "No submissions for this task."
            return "\n".join(
                f"{s.status} | {s.created_at} | id={s.submission_id} | "
                f"human_confirmed={s.human_confirmed} | ai_assisted={s.ai_assisted}"
                for s in submissions
            )
        if action == "/review" and len(parts) in {3, 4}:
            if parts[2] not in {"accepted", "rework"}:
                return "Review decision must be accepted or rework."
            submission = workbench.review(
                owner_id, parts[1], decision=parts[2],
                notes=parts[3] if len(parts) == 4 else "",
            )
            return "Submission not found." if submission is None else (
                f"Submission {submission.submission_id}: {submission.status}."
            )
    except (ValueError, TypeError, json.JSONDecodeError):
        return "Command rejected. Check the syntax, payload schema and current task version."
    except PermissionError:
        return "Action denied by the project assistance or human-confirmation policy."
    except AnnotationConflict:
        return "Task version conflict: reload /task TASK_ID and retry with the current version."
    except Exception:
        return "Workbench action failed safely. Refresh the project/task before retrying."
    return "Unknown command or invalid arguments. Type /help."



def _positive_version(value: str) -> int:
    version = int(value)
    if version < 1:
        raise ValueError("invalid_version")
    return version


def run_annotation_workbench(workbench: Any, owner_id: str) -> None:
    """Launch the optional Textual workbench against an injected store and owner."""
    if workbench is None:
        raise ValueError("annotation_workbench_required")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("annotation_owner_id_required")
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Vertical
        from textual.widgets import Footer, Header, Input, RichLog, Static
    except ImportError as exc:
        raise RuntimeError(
            "Install the optional UI dependency with: pip install -r requirements-ui.txt"
        ) from exc

    class AnnotationApp(App):
        TITLE = "Yuki — Data Annotation Workbench"
        CSS = """
        Screen { padding: 1; }
        #intro { height: auto; padding: 0 1; }
        #output { height: 1fr; border: round $primary; }
        #command { dock: bottom; }
        """
        open_project_id = ""

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                yield Static(
                    "Owner-scoped annotation workspace · human-confirmed submissions only",
                    id="intro",
                )
                yield RichLog(id="output", wrap=True, markup=False)
                yield Input(
                    placeholder="/help · /projects · /open PROJECT_ID · /tasks",
                    id="command",
                )
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#output", RichLog).write(
                "Ready. No tasks are claimed and no submissions are sent automatically. "
                "Use /help to see commands."
            )

        def on_input_submitted(self, event: Input.Submitted) -> None:
            command = event.value.strip()
            event.input.value = ""
            log = self.query_one("#output", RichLog)
            if command == "/quit":
                self.exit()
                return
            if command.startswith("/open "):
                try:
                    candidate = shlex.split(command)
                    if len(candidate) == 2 and workbench.get_project(owner_id, candidate[1]):
                        self.open_project_id = candidate[1]
                except (ValueError, TypeError):
                    pass
            result = execute_annotation_command(
                workbench, owner_id, command, open_project_id=self.open_project_id,
            )
            log.write(f"> {command}\n{result}")

    AnnotationApp().run()
