"""Optional Textual supervision UI for status and explicitly safe commands."""
from __future__ import annotations


def run_dashboard(agent=None, *, command_handler=None) -> None:
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
                yield Input(placeholder="Enter /help, /status, /skills, or /quit", id="command")
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
