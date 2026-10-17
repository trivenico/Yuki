"""Optional Textual supervision dashboard for registered skills and local controls.

The dashboard does not execute arbitrary tools; it displays status and offers a
safe command prompt. Sensitive actions remain subject to the runtime's approval gate.
"""
from __future__ import annotations


def run_dashboard(agent=None) -> None:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Vertical
        from textual.widgets import Footer, Header, Input, Label, RichLog
    except ImportError as exc:
        raise RuntimeError("Install optional Textual with: pip install textual") from exc

    class YukiDashboard(App):
        TITLE = "Yuki — Supervision"
        CSS = "Screen { padding: 1; } #log { height: 1fr; border: round $primary; } #command { dock: bottom; }"

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                yield Label("Yuki supervision console · no background tools run automatically")
                yield RichLog(id="log", wrap=True, markup=False)
                yield Input(placeholder="Enter /help or a safe status command", id="command")
            yield Footer()

        def on_mount(self):
            self.query_one("#log", RichLog).write("Yuki dashboard started. Use /help for available controls.")

        def on_input_submitted(self, event: Input.Submitted):
            command = event.value.strip()
            event.input.value = ""
            log = self.query_one("#log", RichLog)
            if command == "/help":
                log.write("/status · /skills · /quit")
            elif command == "/status":
                if agent is None:
                    log.write("No agent supplied.")
                else:
                    log.write(f"Registered skills: {', '.join(agent.registry.names())}")
            elif command == "/skills":
                if agent is None:
                    log.write("No agent supplied.")
                else:
                    for skill in agent.available_skills():
                        log.write(f"{skill['name']} [{skill['risk']}]: {skill['description']}")
            elif command == "/quit":
                self.exit()
            else:
                log.write("Command not supported by the dashboard. No action was executed.")

    YukiDashboard().run()
