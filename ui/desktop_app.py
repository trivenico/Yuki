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

from core.settings_store import BOOLEAN_SETTINGS, MODEL_SETTING_BY_PROVIDER, PROVIDERS, read_user_settings, save_user_settings
from core.perception import (
    PerceptionUnavailable,
    active_window_title,
    capture_camera_frame,
    capture_screen,
    get_screen_context,
)

ROOT = Path(__file__).resolve().parents[1]
MAX_TRANSCRIPT_CHARS = 1_000_000
MAX_COMMAND_HISTORY = 500


class CommandHistory:
    """Bounded in-memory command history with familiar Up/Down navigation."""

    def __init__(self, limit: int = MAX_COMMAND_HISTORY) -> None:
        if limit < 1:
            raise ValueError("Command history limit must be positive.")
        self._limit = limit
        self._commands: list[str] = []
        self._position: int | None = None

    def add(self, command: str) -> None:
        value = command.strip()
        if not value:
            return
        if not self._commands or self._commands[-1] != value:
            self._commands.append(value)
            if len(self._commands) > self._limit:
                del self._commands[:-self._limit]
        self._position = None

    def move(self, direction: int) -> str:
        if direction not in (-1, 1):
            raise ValueError("History direction must be -1 or 1.")
        if not self._commands:
            return ""
        if self._position is None:
            self._position = len(self._commands)
        if direction == -1 and self._position == 0:
            return ""
        self._position = max(0, min(len(self._commands), self._position + direction))
        if self._position == len(self._commands):
            return ""
        return self._commands[self._position]


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


def provider_environment_override(
    selected_provider: str,
    selected_model: str,
    *,
    environ=None,
) -> str | None:
    """Explain when environment overrides make desktop provider settings ineffective."""
    env = os.environ if environ is None else environ
    selected = selected_provider.strip().lower()
    if "YUKI_PROVIDER" in env:
        provider_override = str(env["YUKI_PROVIDER"]).strip().lower()
        if provider_override != selected:
            shown_provider = provider_override or "(empty)"
            return (
                f"YUKI_PROVIDER is set to '{shown_provider}', so the runtime will ignore "
                f"the UI selection '{selected}' or fail provider selection. Remove or update "
                "YUKI_PROVIDER and restart Yuki."
            )

    model_env_by_provider = {
        "ollama": "YUKI_OLLAMA_MODEL",
        "openai": "YUKI_OPENAI_MODEL",
        "gemini": "YUKI_GEMINI_MODEL",
        "hybrid": "YUKI_CLOUD_MODEL",
        "anthropic": "YUKI_ANTHROPIC_MODEL",
        "deepseek": "YUKI_DEEPSEEK_MODEL",
        "kimi": "YUKI_KIMI_MODEL",
        "minimax": "YUKI_MINIMAX_MODEL",
        "openrouter": "YUKI_OPENROUTER_MODEL",
        "tokenrouter": "YUKI_TOKENROUTER_MODEL",
    }
    model_env_name = model_env_by_provider.get(selected)
    if model_env_name and model_env_name in env:
        environment_model = str(env[model_env_name]).strip()
        if environment_model != selected_model.strip():
            shown_model = environment_model or "(empty)"
            return (
                f"{model_env_name} is set to '{shown_model}', so the runtime will ignore "
                "the model entered in Settings or fail model selection. Remove or update "
                "that environment variable and restart Yuki."
            )
    return None


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

    workflow_bar = ttk.LabelFrame(main_panel, text="Workflow shortcuts", padding=(8, 5))
    workflow_bar.pack(fill="x", pady=(8, 0))

    entry_row = ttk.Frame(main_panel)
    entry_row.pack(fill="x", pady=(6, 0))
    command_entry = ttk.Entry(entry_row)
    command_entry.pack(side="left", fill="x", expand=True)
    send_button = ttk.Button(entry_row, text="Run / Send")
    send_button.pack(side="left", padx=(8, 0))
    ttk.Label(main_panel, text="Enter to send · ↑/↓ command history · Ctrl+L clears the visible transcript").pack(anchor="w", pady=(4, 0))

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
        ("Interactive Session", focus_composer),
        ("Tasks & Plans", lambda: send_text("/tasks")),
        ("Automation", lambda: send_text("/reminders")),
        ("Developer Workflows", lambda: insert_command("/knowledge index ")),
        ("Memory", lambda: send_text("/memory list")),
        ("Skills", lambda: send_text("/skills")),
        ("Plugins", lambda: send_text("/plugins")),
        ("Voice", lambda: send_text("/voice status")),
        ("Security", lambda: send_text("/security status")),
        ("System Readiness", lambda: send_text("/readiness")),
        ("Self-Evolution", lambda: send_text("/evolution status")),
        ("AI Agent Council", lambda: send_text("/council status")),
        ("Screen & Camera", lambda: open_perception()),
    )
    for label, action in nav_items:
        ttk.Button(sidebar, text=label, command=action).pack(fill="x", pady=2)
    ttk.Separator(sidebar).pack(fill="x", pady=8)
    ttk.Label(
        sidebar,
        text="Commands and approvals run through the shared guarded runtime.",
        wraplength=145,
        justify="left",
    ).pack(anchor="w", pady=(14, 0))

    def add_shortcut(label: str, command: str) -> None:
        ttk.Button(
            workflow_bar,
            text=label,
            command=lambda value=command: insert_command(value),
        ).pack(side="left", padx=(0, 5), pady=1)

    add_shortcut("Plan task", "/plan ")
    add_shortcut("Task command", "/task ")
    add_shortcut("Reminders", "/reminders")
    add_shortcut("Add reminder", "/reminder add ")
    add_shortcut("Index file", "/knowledge index ")
    add_shortcut("Morning briefing", "/briefing")
    add_shortcut("Evolution review", "/evolution status")
    add_shortcut("Evolution proposals", "/evolution report")
    add_shortcut("Run AI Council", "/council run Review the current project goal and propose next steps")
    ttk.Button(workflow_bar, text="Screen & camera", command=lambda: open_perception()).pack(side="left", padx=(0, 5), pady=1)
    add_shortcut("Task history", "/tasks")
    add_shortcut("Runtime status", "/status")
    add_shortcut("Help / commands", "/help")

    command_history = CommandHistory()

    def navigate_history(direction: int) -> str:
        value = command_history.move(direction)
        command_entry.delete(0, "end")
        command_entry.insert(0, value)
        command_entry.icursor("end")
        return "break"

    def history_up(_event=None) -> str:
        return navigate_history(-1)

    def history_down(_event=None) -> str:
        return navigate_history(1)

    def clear_visible_transcript(_event=None) -> str:
        transcript.configure(state="normal")
        transcript.delete("1.0", "end")
        transcript.configure(state="disabled")
        return "break"

    command_entry.bind("<Up>", history_up)
    command_entry.bind("<Down>", history_down)
    root.bind("<Control-l>", clear_visible_transcript)
    root.bind("<Control-L>", clear_visible_transcript)

    def send_command(_event=None) -> None:
        command = command_entry.get()
        if not command.strip():
            return
        command_history.add(command)
        send_text(command)
        command_entry.delete(0, "end")

    def choose_manifest(kind: str) -> None:
        if kind == "agent_skill":
            path = filedialog.askopenfilename(
                parent=root,
                title="Choose an Agent Skills SKILL.md file",
                filetypes=[("Agent Skills", "SKILL.md"), ("Markdown files", "*.md"), ("All files", "*.*")],
            )
        else:
            path = filedialog.askopenfilename(
                parent=root,
                title="Choose a Yuki skill manifest" if kind == "skill" else "Choose a Yuki MCP plugin manifest",
                filetypes=[("JSON manifests", "*.json"), ("All files", "*.*")],
            )
        if not path:
            return
        if kind == "agent_skill":
            send_text(f'/skills import "{path}"')
            return
        command_group = "skills" if kind == "skill" else "plugins"
        send_text(f'/{command_group} install "{path}"')

    def open_perception() -> None:
        """Explicit one-shot local captures; no background polling or persistence."""
        try:
            current = read_user_settings()
        except Exception as exc:
            messagebox.showerror("Perception unavailable", f"Could not read privacy settings ({type(exc).__name__}).", parent=root)
            return

        window = tk.Toplevel(root)
        window.title("Yuki — Screen & Camera")
        window.transient(root)
        window.geometry("620x520")
        window.minsize(520, 400)
        panel = ttk.Frame(window, padding=14)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Local perception", font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(
            panel,
            text="Captures happen only when you click a button. Frames are held in memory for preview and are not saved or uploaded by this feature. Close this window to discard the preview.",
            wraplength=560,
        ).pack(anchor="w", pady=(5, 10))
        status = tk.StringVar(value="Idle — no capture active.")
        ttk.Label(panel, textvariable=status, wraplength=560).pack(anchor="w", pady=(0, 8))
        preview = ttk.Label(panel, anchor="center", text="No image captured")
        preview.pack(fill="both", expand=True, pady=6)
        preview_image = {"value": None}

        def show_capture(image, label: str) -> None:
            try:
                from PIL import ImageTk
                image = image.copy()
                image.thumbnail((560, 330))
                photo = ImageTk.PhotoImage(image)
                preview_image["value"] = photo
                preview.configure(image=photo, text="")
                status.set(label)
            except Exception as exc:
                status.set(f"Preview unavailable ({type(exc).__name__}); capture was not saved.")

        def take_screen() -> None:
            try:
                image = capture_screen(enabled=current["screen_observation_enabled"])
                show_capture(image, "One screen snapshot captured locally. No continuous observation is running.")
            except (PermissionError, PerceptionUnavailable) as exc:
                messagebox.showwarning("Screen capture unavailable", str(exc), parent=window)

        def take_camera() -> None:
            try:
                image = capture_camera_frame(enabled=current["camera_access_enabled"])
                show_capture(image, "One camera frame captured locally. Camera released; no recording is running.")
            except (PermissionError, PerceptionUnavailable, ValueError) as exc:
                messagebox.showwarning("Camera unavailable", str(exc), parent=window)

        def read_window() -> None:
            try:
                context = get_screen_context(enabled=current["screen_observation_enabled"])
                status.set("Active window metadata: " + (context.active_window or "Unavailable on this platform"))
            except PermissionError as exc:
                messagebox.showwarning("Screen observation disabled", str(exc), parent=window)

        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=(4, 0))
        ttk.Button(buttons, text="Capture screen once", command=take_screen).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Camera snapshot", command=take_camera).pack(side="left", padx=6)
        ttk.Button(buttons, text="Active window", command=read_window).pack(side="left", padx=6)
        ttk.Button(buttons, text="Privacy settings", command=lambda: (window.destroy(), open_settings())).pack(side="right")

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
            values=PROVIDERS, state="readonly", width=20,
        )
        provider_picker.grid(row=1, column=0, sticky="ew", pady=(3, 8))

        ttk.Label(ai_group, text="Ollama model").grid(row=2, column=0, sticky="w")
        model_value = tk.StringVar(value=current["ollama_model"])
        ttk.Entry(ai_group, textvariable=model_value, width=34).grid(row=3, column=0, sticky="ew", pady=(3, 8))

        ttk.Label(ai_group, text="Selected cloud provider model").grid(row=4, column=0, sticky="w")
        cloud_model_values = {
            key: current.get(key, "") for key in MODEL_SETTING_BY_PROVIDER.values()
        }
        cloud_model_key = MODEL_SETTING_BY_PROVIDER.get(provider_value.get())
        selected_model_key = {"value": cloud_model_key}
        cloud_model_value = tk.StringVar(value=cloud_model_values.get(cloud_model_key, "") if cloud_model_key else "")
        cloud_model_entry = ttk.Entry(ai_group, textvariable=cloud_model_value, width=34)
        cloud_model_entry.grid(row=5, column=0, sticky="ew", pady=(3, 8))
        if not cloud_model_key:
            cloud_model_entry.configure(state="disabled")

        provider_help = tk.StringVar()
        ttk.Label(ai_group, textvariable=provider_help, wraplength=410).grid(row=6, column=0, sticky="w", pady=(0, 6))

        def update_provider_help(*_args) -> None:
            notes = {
                "ollama": "Local by default. Requires Ollama running and the selected model already installed.",
                "openai": "Cloud provider: prompts leave this device. Set OPENAI_API_KEY in the launch environment; keys are never saved here.",
                "gemini": "Uses the installed Gemini CLI and its own authentication. Check its setup separately.",
                "hybrid": "Uses local Ollama by default; cloud routing requires explicit environment configuration and allow-listed task names.",
                "anthropic": "Cloud provider: set ANTHROPIC_API_KEY in the launch environment. Model name is saved locally; credentials are not.",
                "deepseek": "Cloud provider: set DEEPSEEK_API_KEY in the launch environment. Model name is saved locally; credentials are not.",
                "kimi": "Cloud provider: set MOONSHOT_API_KEY in the launch environment. Model name is saved locally; credentials are not.",
                "minimax": "Cloud provider: set MINIMAX_API_KEY in the launch environment. Model name is saved locally; credentials are not.",
                "openrouter": "Third-party model gateway: set OPENROUTER_API_KEY in the launch environment. Prompts go through that service.",
                "tokenrouter": "Third-party model gateway: set TOKENROUTER_API_KEY in the launch environment. Prompts go through that service.",
            }
            previous_key = selected_model_key["value"]
            if previous_key:
                cloud_model_values[previous_key] = cloud_model_value.get()
            model_key = MODEL_SETTING_BY_PROVIDER.get(provider_value.get())
            selected_model_key["value"] = model_key
            cloud_model_value.set(cloud_model_values.get(model_key, "") if model_key else "")
            cloud_model_entry.configure(state="normal" if model_key else "disabled")
            provider_help.set(notes.get(provider_value.get(), "Select a provider."))
        provider_value.trace_add("write", update_provider_help)
        update_provider_help()

        def check_provider() -> None:
            selected = provider_value.get()
            selected_model = model_value.get().strip() if selected == "ollama" else cloud_model_value.get().strip()
            override_message = provider_environment_override(selected, selected_model)
            if override_message:
                messagebox.showwarning(
                    "Settings overridden by environment",
                    override_message,
                    parent=window,
                )
                return
            try:
                from core.config import load_config
                from providers.factory import create_provider
                config = load_config()
                config["provider"] = selected
                config["ollama_model"] = model_value.get().strip()
                model_key = MODEL_SETTING_BY_PROVIDER.get(selected)
                if model_key:
                    config[model_key] = cloud_model_value.get().strip()
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
            "background_evolution_enabled": "Enable local background evolution reviews (proposals only)",
            "agent_council_enabled": "Enable parallel Yuki Council for task planning",
            "screen_observation_enabled": "Allow explicit local screen observation",
            "camera_access_enabled": "Allow explicit camera snapshots (no recording)",
            "desktop_control_enabled": "Allow approved desktop control actions (not yet implemented)",
        }
        groups = (
            ("Voice", ("voice_enabled", "wakeword_enabled")),
            ("Privacy & Security", ("security_lab_enabled", "sandbox_enabled", "firewall_enabled", "firewall_dry_run")),
            ("Integrations", ("gmail_enabled", "plugin_discovery_enabled")),
            ("Self-Evolution", ("background_evolution_enabled",)),
            ("AI Agent Council", ("agent_council_enabled",)),
            ("Perception & Desktop", ("screen_observation_enabled", "camera_access_enabled", "desktop_control_enabled")),
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
        ).grid(row=len(groups) + 1, column=0, columnspan=2, sticky="w", pady=(0, 8))

        def save_settings() -> None:
            changes = {
                "provider": provider_value.get(),
                "ollama_model": model_value.get(),
            }
            model_key = MODEL_SETTING_BY_PROVIDER.get(provider_value.get())
            if model_key:
                changes[model_key] = cloud_model_value.get()
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
        actions.grid(row=len(groups) + 2, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="right")
        ttk.Button(actions, text="Save Settings", command=save_settings).pack(side="right", padx=(0, 8))

    ttk.Button(sidebar, text="Settings", command=open_settings).pack(fill="x", pady=2)
    ttk.Button(extension_row, text="Settings", command=open_settings).pack(side="right", padx=(6, 0))
    def choose_skill_directory() -> None:
        directory = filedialog.askdirectory(
            parent=root,
            title="Choose a skill directory containing SKILL.md",
            mustexist=True,
        )
        if directory:
            send_text(f'/skills import "{directory}"')

    ttk.Button(extension_row, text="Install Plugin", command=lambda: choose_manifest("plugin")).pack(side="right", padx=(6, 0))
    ttk.Button(extension_row, text="Install Skill", command=lambda: choose_manifest("skill")).pack(side="right", padx=(6, 0))
    ttk.Button(extension_row, text="Import Skill Folder", command=choose_skill_directory).pack(side="right", padx=(6, 0))
    ttk.Button(extension_row, text="Import Skill", command=lambda: choose_manifest("agent_skill")).pack(side="right", padx=(6, 0))
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
