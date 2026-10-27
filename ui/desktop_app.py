"""Cross-platform desktop shell for the same guarded CLI runtime.

The GUI does not implement a second tool execution path. It launches main.py as
a child process and forwards typed commands/approval answers to that runtime.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from core.settings_store import BOOLEAN_SETTINGS, read_user_settings, save_user_settings

ROOT = Path(__file__).resolve().parents[1]
MAX_TRANSCRIPT_CHARS = 1_000_000


def build_runtime_command() -> list[str]:
    """Return a shell-free command for the authoritative guarded runtime."""
    if getattr(sys, "frozen", False):
        runtime_name = "YukiRuntime.exe" if os.name == "nt" else "YukiRuntime"
        install_root = Path(sys.executable).resolve().parent.parent
        runtime = install_root / "YukiRuntime" / runtime_name
        if not runtime.is_file():
            raise RuntimeError(
                f"Packaged runtime was not found in the adjacent {runtime.parent.name} directory: {runtime_name}. "
                "Reinstall Yuki or launch the CLI runtime directly."
            )
        return [str(runtime)]
    return [sys.executable, "-u", str(ROOT / "main.py")]


def run_desktop() -> None:
    """Run the desktop console; operational commands retain CLI approval gates."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise RuntimeError(
            "Tkinter is unavailable. Install your operating system's Python Tk support, "
            "or use the CLI with: python yuki_cli.py start."
        ) from exc

    root = tk.Tk()
    root.title("Yuki — Desktop")
    root.geometry("1180x760")
    root.minsize(820, 560)

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)
    header = ttk.Frame(frame)
    header.pack(fill="x", pady=(0, 10))
    ttk.Label(header, text="Yuki", font=("TkDefaultFont", 16, "bold")).pack(side="left")
    ttk.Label(header, text="Your AI workspace · local-first, approval-aware").pack(side="left", padx=(12, 0))
    workspace = ttk.Frame(frame)
    workspace.pack(fill="both", expand=True)
    sidebar = ttk.Frame(workspace, padding=(0, 0, 10, 0), width=170)
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)
    ttk.Label(sidebar, text="WORKSPACE", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(2, 8))
    main_panel = ttk.Frame(workspace)
    main_panel.pack(side="left", fill="both", expand=True)
    extension_row = ttk.Frame(main_panel)
    extension_row.pack(fill="x", pady=(0, 8))

    output_frame = ttk.Frame(main_panel)
    output_frame.pack(side="top", fill="both", expand=True)
    transcript = tk.Text(output_frame, wrap="word", state="disabled", undo=False)
    transcript.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(output_frame, orient="vertical", command=transcript.yview)
    transcript.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")

    entry_row = ttk.Frame(main_panel)
    entry_row.pack(fill="x", pady=(8, 0))
    command_entry = ttk.Entry(entry_row)
    command_entry.pack(side="left", fill="x", expand=True)
    send_button = ttk.Button(entry_row, text="Send")
    send_button.pack(side="left", padx=(8, 0))

    output_queue: queue.Queue[str | None] = queue.Queue(maxsize=100_000)
    try:
        process = subprocess.Popen(
            build_runtime_command(),
            cwd=str(
                Path(sys.executable).resolve().parent.parent
                if getattr(sys, "frozen", False)
                else ROOT
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt" and getattr(sys, "frozen", False)
                else 0
            ),
            shell=False,
        )
    except OSError as exc:
        messagebox.showerror("Yuki could not start", f"Runtime launch failed ({type(exc).__name__}).")
        root.destroy()
        return

    def read_output() -> None:
        stream = process.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            while True:
                char = stream.read(1)
                if char == "":
                    break
                output_queue.put(char)
        finally:
            output_queue.put(None)

    threading.Thread(target=read_output, name="yuki-desktop-output", daemon=True).start()

    def append_output(value: str) -> None:
        transcript.configure(state="normal")
        transcript.insert("end", value)
        current_count = transcript.count("1.0", "end-1c", "chars")
        if current_count and current_count[0] > MAX_TRANSCRIPT_CHARS:
            excess = current_count[0] - MAX_TRANSCRIPT_CHARS
            transcript.delete("1.0", f"1.0+{excess}c")
        transcript.see("end")
        transcript.configure(state="disabled")

    def drain_output() -> None:
        chunks = []
        finished = False
        while True:
            try:
                item = output_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                finished = True
                break
            chunks.append(item)
        if chunks:
            append_output("".join(chunks))
        if finished:
            send_button.configure(state="disabled")
            command_entry.configure(state="disabled")
            append_output("\n[Yuki runtime has exited.]\n")
            return
        root.after(35, drain_output)

    def send_text(command: str) -> None:
        if not command.strip():
            return
        if process.poll() is not None or process.stdin is None:
            append_output("\n[Yuki runtime is not running.]\n")
            return
        try:
            process.stdin.write(command + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            append_output("\n[Could not send input to the Yuki runtime.]\n")
            return
        # A pipe does not echo terminal input, so echo it here for visibility.
        append_output(command + "\n")

    def focus_composer() -> None:
        command_entry.focus_set()

    def insert_command(value: str) -> None:
        command_entry.delete(0, "end")
        command_entry.insert(0, value)
        command_entry.focus_set()

    nav_items = (
        ("Chat", focus_composer),
        ("Tasks & Plans", lambda: insert_command("/plan ")),
        ("Memory", lambda: send_text("/memory list")),
        ("Skills", lambda: send_text("/skills")),
        ("Plugins", lambda: send_text("/plugins")),
        ("Voice", lambda: send_text("/voice status")),
        ("Security", lambda: send_text("/security status")),
        ("System Readiness", lambda: send_text("/readiness")),
    )
    for label, action in nav_items:
        ttk.Button(sidebar, text=label, command=action).pack(fill="x", pady=2)
    ttk.Separator(sidebar).pack(fill="x", pady=8)
    ttk.Button(sidebar, text="Settings", command=open_settings).pack(fill="x", pady=2)
    ttk.Label(
        sidebar,
        text="Commands and approvals run through the shared guarded runtime.",
        wraplength=145,
        justify="left",
    ).pack(anchor="w", pady=(14, 0))

    def send_command(_event=None) -> None:
        command = command_entry.get()
        if not command.strip():
            return
        send_text(command)
        command_entry.delete(0, "end")

    def choose_manifest(kind: str) -> None:
        path = filedialog.askopenfilename(
            parent=root,
            title="Choose a Yuki skill manifest" if kind == "skill" else "Choose a Yuki MCP plugin manifest",
            filetypes=[("JSON manifests", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        command_group = "skills" if kind == "skill" else "plugins"
        send_text(f'/{command_group} install "{path}"')

    def open_settings() -> None:
        window = tk.Toplevel(root)
        window.title("Yuki Settings")
        window.transient(root)
        window.resizable(False, False)
        panel = ttk.Frame(window, padding=16)
        panel.pack(fill="both", expand=True)
        try:
            current = read_user_settings()
        except Exception as exc:
            messagebox.showerror("Settings unavailable", str(exc), parent=window)
            window.destroy()
            return

        ai_group = ttk.LabelFrame(panel, text="General & AI", padding=10)
        ai_group.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(ai_group, text="Provider").grid(row=0, column=0, sticky="w")
        provider_value = tk.StringVar(value=current["provider"])
        provider_picker = ttk.Combobox(
            ai_group, textvariable=provider_value,
            values=("ollama", "openai", "gemini", "hybrid"), state="readonly", width=20,
        )
        provider_picker.grid(row=1, column=0, sticky="ew", pady=(3, 8))

        ttk.Label(ai_group, text="Ollama model").grid(row=2, column=0, sticky="w")
        model_value = tk.StringVar(value=current["ollama_model"])
        ttk.Entry(ai_group, textvariable=model_value, width=34).grid(row=3, column=0, sticky="ew", pady=(3, 8))

        ttk.Label(ai_group, text="OpenAI-compatible model").grid(row=4, column=0, sticky="w")
        cloud_model_value = tk.StringVar(value=current["openai_model"])
        ttk.Entry(ai_group, textvariable=cloud_model_value, width=34).grid(row=5, column=0, sticky="ew", pady=(3, 8))

        provider_help = tk.StringVar()
        ttk.Label(ai_group, textvariable=provider_help, wraplength=410).grid(row=6, column=0, sticky="w", pady=(0, 6))

        def update_provider_help(*_args) -> None:
            notes = {
                "ollama": "Local by default. Requires Ollama running and the selected model already installed.",
                "openai": "Cloud provider: prompts leave this device. Set OPENAI_API_KEY in the launch environment; keys are never saved here.",
                "gemini": "Uses the installed Gemini CLI and its own authentication. Check its setup separately.",
                "hybrid": "Uses local Ollama by default; cloud routing requires explicit environment configuration and allow-listed task names.",
            }
            provider_help.set(notes.get(provider_value.get(), "Select a provider."))
        provider_value.trace_add("write", update_provider_help)
        update_provider_help()

        def check_provider() -> None:
            selected = provider_value.get()
            try:
                from core.config import load_config
                from providers.factory import create_provider
                config = load_config()
                config["provider"] = selected
                config["ollama_model"] = model_value.get().strip()
                config["openai_model"] = cloud_model_value.get().strip()
                provider = create_provider(config)
                available = provider.is_available()
            except Exception as exc:
                messagebox.showerror("Provider check failed", f"Could not check provider ({type(exc).__name__}): {str(exc)[:240]}", parent=window)
                return
            if available:
                messagebox.showinfo("Provider check", f"{selected} reports ready. This check does not send a prompt.", parent=window)
            else:
                messagebox.showwarning("Provider not ready", f"{selected} is not ready. Check its local service, model installation, CLI authentication, or environment credentials. No model was downloaded.", parent=window)

        ttk.Button(ai_group, text="Check provider readiness", command=check_provider).grid(row=7, column=0, sticky="w", pady=(2, 0))
        ttk.Label(
            ai_group,
            text="Changing provider takes effect after restart. Model selection does not download models. Cloud providers may transmit prompts to their service.",
            wraplength=410,
        ).grid(row=8, column=0, sticky="w", pady=(8, 0))

        labels = {
            "voice_enabled": "Enable optional voice features",
            "security_lab_enabled": "Enable security lab tools",
            "sandbox_enabled": "Enable container sandbox",
            "gmail_enabled": "Enable Gmail integration",
            "firewall_enabled": "Enable firewall controls",
            "firewall_dry_run": "Firewall dry-run mode (recommended)",
            "wakeword_enabled": "Enable wake-word feature",
            "plugin_discovery_enabled": "Enable plugin discovery",
        }
        groups = (
            ("Voice", ("voice_enabled", "wakeword_enabled")),
            ("Privacy & Security", ("security_lab_enabled", "sandbox_enabled", "firewall_enabled", "firewall_dry_run")),
            ("Integrations", ("gmail_enabled", "plugin_discovery_enabled")),
        )
        variables = {}
        for row, (title, keys) in enumerate(groups, start=1):
            group = ttk.LabelFrame(panel, text=title, padding=10)
            group.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            for key in keys:
                variable = tk.BooleanVar(value=current[key])
                variables[key] = variable
                ttk.Checkbutton(group, text=labels[key], variable=variable).pack(anchor="w", pady=2)
        ttk.Label(
            panel,
            text="Optional features may need extra dependencies or credentials. Environment variables can override saved settings. Restart Yuki after saving.",
            wraplength=430,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))

        def save_settings() -> None:
            changes = {
                "provider": provider_value.get(),
                "ollama_model": model_value.get(),
                "openai_model": cloud_model_value.get(),
            }
            changes.update({key: variable.get() for key, variable in variables.items()})
            try:
                save_user_settings(changes)
            except (ValueError, RuntimeError, OSError) as exc:
                messagebox.showerror("Could not save settings", str(exc), parent=window)
                return
            messagebox.showinfo(
                "Settings saved",
                "Settings were saved to your user configuration. Restart Yuki to apply changes. "
                "Model downloads and sensitive actions still require explicit confirmation.",
                parent=window,
            )
            window.destroy()

        actions = ttk.Frame(panel)
        actions.grid(row=5, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="right")
        ttk.Button(actions, text="Save Settings", command=save_settings).pack(side="right", padx=(0, 8))

    ttk.Button(extension_row, text="Settings", command=open_settings).pack(side="right", padx=(6, 0))
    ttk.Button(extension_row, text="Install Plugin", command=lambda: choose_manifest("plugin")).pack(side="right", padx=(6, 0))
    ttk.Button(extension_row, text="Install Skill", command=lambda: choose_manifest("skill")).pack(side="right", padx=(6, 0))
    ttk.Button(extension_row, text="Readiness", command=lambda: send_text("/readiness")).pack(side="right", padx=(6, 0))
    ttk.Button(extension_row, text="Help", command=lambda: send_text("/help")).pack(side="right", padx=(6, 0))

    def close_window() -> None:
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("/quit\n")
                    process.stdin.flush()
                process.wait(timeout=1.5)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    process.kill()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        root.destroy()

    send_button.configure(command=send_command)
    command_entry.bind("<Return>", send_command)
    root.protocol("WM_DELETE_WINDOW", close_window)
    command_entry.focus_set()
    root.after(35, drain_output)
    root.mainloop()


if __name__ == "__main__":
    run_desktop()
