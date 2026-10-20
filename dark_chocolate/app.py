"""DarkChocolate Rich CLI composition."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
from loguru import logger
from rich.console import Console
from rich.prompt import Confirm, Prompt
from .audit import AuditLogger
from .llm import OllamaClient
from .react_loop import ReActLoop
from .security import ApprovalGate, Risk
from .tools import Tool, ToolRegistry
from .workflow import make_plan
console = Console()
def build_app() -> ReActLoop:
    audit = AuditLogger(os.getenv("DARK_CHOCOLATE_AUDIT_PATH", "data/logs/audit.jsonl"))
    gate = ApprovalGate(lambda reason: Confirm.ask(reason, default=False))
    registry = ToolRegistry(audit, gate)
    workspace = Path(os.getenv("DARK_CHOCOLATE_WORKSPACE", "workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    def write_report(args: dict[str, object]) -> str:
        name, content = str(args.get("name", "report.md")), str(args.get("content", ""))
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("report name must be a plain filename")
        if len(content) > 100_000:
            raise ValueError("report too large")
        target = (workspace / name).resolve()
        if target.parent != workspace:
            raise ValueError("path escapes workspace")
        target.write_text(content, encoding="utf-8")
        return f"Report written to {target}"
    registry.register(Tool("write_report", "Write a report inside the configured workspace", Risk.MEDIUM, write_report))
    client = OllamaClient(os.getenv("DARK_CHOCOLATE_OLLAMA_URL", "http://127.0.0.1:11434"), os.getenv("DARK_CHOCOLATE_MODEL", "llama3.1:8b"))
    return ReActLoop(client, registry, audit, max_iterations=10)
async def run_cli() -> None:
    agent = build_app()
    console.print("[bold chocolate1]DarkChocolate[/bold chocolate1] — local-first prototype")
    console.print("Type /exit to quit.")
    while True:
        try:
            text = Prompt.ask("[bold cyan]You[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            break
        if text.strip() == "/exit":
            break
        plan = make_plan(text)
        console.print("\n[bold]Proposed workflow[/bold]")
        for i, step in enumerate(plan.steps, 1):
            console.print(f"{i}. {step.goal}")
        try:
            plan.begin(Confirm.ask("Approve plan and send request to local model?", default=False))
        except PermissionError:
            console.print("[yellow]Plan declined; nothing executed.[/yellow]")
            continue
        try:
            answer = await agent.run(text)
            plan.complete()
            console.print(f"\n[bold green]DarkChocolate[/bold green]: {answer}\n")
        except Exception:
            logger.exception("request failed")
            console.print("[red]Request failed. See local logs.[/red]")
def main() -> None:
    logger.remove()
    logger.add("data/logs/dark_chocolate.log", rotation="5 MB", retention=5, level="INFO")
    asyncio.run(run_cli())
