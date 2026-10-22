"""Optional Textual supervision UI with read-only host performance telemetry."""
from __future__ import annotations


def run_dashboard(agent=None, *, command_handler=None, token_usage=None, provider=None, reconciliation=None, operator="local-owner") -> None:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Vertical
        from textual.widgets import Footer, Header, Input, Label, RichLog
    except ImportError as exc:
        raise RuntimeError("Install optional Textual with: pip install -r requirements-ui.txt") from exc

    from core.performance_monitor import collect_performance, format_performance_report
    from core.performance_history import format_performance_history
    from core.device_diagnostics import discover_devices, format_device_report

    class YukiDashboard(App):
        TITLE = "Yuki — Supervision"
        CSS = "Screen { padding: 1; } #log { height: 1fr; border: round $primary; } #command { dock: bottom; }"
        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                yield Label("Yuki supervision console · sensitive actions remain approval-gated")
                yield Label("Performance telemetry loading…", id="performance_status")
                yield RichLog(id="log", wrap=True, markup=False)
                yield Input(placeholder="/help /status /skills /performance /performance-history /devices /reconcile list /reconcile resolve KEY DISPOSITION | EVIDENCE /quit", id="command")
            yield Footer()

        def on_mount(self):
            self._reconciliation_confirmation = None
            self.query_one("#log", RichLog).write("Dashboard ready. No background actions are running.")
            self._refresh_performance()
            self.set_interval(2.0, self._refresh_performance)

        def _refresh_performance(self):
            try:
                summary = token_usage.summary() if token_usage is not None else None
                provider_state = vars(provider) if provider is not None else {}
                context_data = provider_state.get("last_context_budget")
                if provider is not None and isinstance(getattr(type(provider), "last_context_budget", None), property):
                    context_data = provider.last_context_budget
                report = format_performance_report(collect_performance(
                    token_summary=summary, context_budget_data=context_data
                ))
                label = self.query_one("#performance_status", Label)
                label.update(report.replace("\\n", "  ·  "))
            except Exception:
                label = self.query_one("#performance_status", Label) if self.query("#performance_status") else None
                if label is not None:
                    label.update("Performance telemetry unavailable; unsupported metrics remain unknown.")

        def on_input_submitted(self, event: Input.Submitted):
            command = event.value.strip()
            event.input.value = ""
            log = self.query_one("#log", RichLog)
            if command == "/quit":
                self.exit()
                return
            if command == "/reconcile list":
                if reconciliation is None:
                    log.write("Reconciliation service unavailable.")
                    return
                try:
                    items = reconciliation.list_pending()
                    if not items:
                        log.write("No executions currently require reconciliation.")
                    for item in items:
                        log.write(
                            f"KEY={item.idempotency_key} STATE={item.state} "
                            f"ACTION_SHA256={item.action_hash} UPDATED={item.updated_at}"
                        )
                except Exception as exc:
                    log.write(f"Reconciliation listing unavailable: {type(exc).__name__}")
                return
            if command.startswith("/reconcile resolve "):
                if reconciliation is None:
                    log.write("Reconciliation service unavailable.")
                    return
                payload = command[len("/reconcile resolve "):]
                if "|" not in payload:
                    log.write("Use /reconcile resolve KEY DISPOSITION | EVIDENCE")
                    return
                header, evidence = payload.split("|", 1)
                parts = header.strip().split(maxsplit=1)
                if len(parts) != 2 or not evidence.strip():
                    log.write("Provide an execution key, disposition, and evidence.")
                    return
                key, disposition = parts
                self._reconciliation_confirmation = (key, disposition, evidence.strip())
                log.write(
                    f"Review KEY={key} DISPOSITION={disposition}. This records a decision only; "
                    "it will not retry or change execution state. Type exactly "
                    f"/reconcile confirm {key} to record it, or anything else to cancel."
                )
                return
            if command.startswith("/reconcile confirm "):
                expected = command[len("/reconcile confirm "):].strip()
                pending = self._reconciliation_confirmation
                self._reconciliation_confirmation = None
                if pending is None or expected != pending[0]:
                    log.write("Reconciliation confirmation cancelled or did not match.")
                    return
                try:
                    decision = reconciliation.resolve(
                        pending[0], operator=operator, disposition=pending[1], evidence=pending[2]
                    )
                    log.write(
                        f"Decision recorded for {decision['idempotency_key']}: "
                        f"{decision['disposition']}. No retry was performed."
                    )
                except Exception as exc:
                    log.write(f"Reconciliation failed safely: {type(exc).__name__}: {exc}")
                return
            if command == "/performance":
                try:
                    summary = token_usage.summary() if token_usage is not None else None
                    provider_state = vars(provider) if provider is not None else {}
                    context_data = provider_state.get("last_context_budget")
                    if provider is not None and isinstance(getattr(type(provider), "last_context_budget", None), property):
                        context_data = provider.last_context_budget
                    log.write(format_performance_report(collect_performance(
                        token_summary=summary, context_budget_data=context_data
                    )))
                except Exception:
                    log.write("Performance diagnostics unavailable; unsupported metrics remain unknown.")
            elif command == "/performance-history":
                log.write(format_performance_history())
            elif command == "/devices":
                try:
                    log.write(format_device_report(discover_devices()))
                except Exception as exc:
                    log.write(f"Device diagnostics unavailable ({type(exc).__name__}).")
            elif command in {"/help", "/status", "/skills"} and command_handler:
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
