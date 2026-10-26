"""Optional Tkinter desktop client for Yuki.

Run with: python desktop_app.py
The UI calls DesktopService, which uses configured real providers and never
executes registered skills or shell commands from chat messages.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from core.desktop_onboarding import load_onboarding_state
from core.desktop_service import DesktopService, DesktopServiceError


class YukiDesktopApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Yuki — Personal AI")
        self.root.geometry("1040x720")
        self.root.minsize(760, 560)
        self.service = DesktopService()
        self.service.mark_welcome_seen()
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.providers = [
            item for item in self.service.list_providers()
            if item["implemented"]
        ]
        self.provider_var = tk.StringVar(
            value=next((p["name"] for p in self.providers if p["selected"]),
                       self.providers[0]["name"] if self.providers else "")
        )
        self.status_var = tk.StringVar(value="Set up a provider to begin.")
        self._build_ui()
        self._refresh_setup()
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self.root, padding=18)
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="YUKI", font=("TkDefaultFont", 22, "bold")).pack(anchor="w")
        ttk.Label(sidebar, text="Your personal AI", foreground="#666666").pack(anchor="w", pady=(0, 24))

        ttk.Label(sidebar, text="SETUP", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(sidebar, text="AI provider").pack(anchor="w")
        self.provider_combo = ttk.Combobox(
            sidebar, textvariable=self.provider_var,
            values=[p["name"] for p in self.providers], state="readonly", width=22
        )
        self.provider_combo.pack(anchor="w", fill="x", pady=(4, 8))
        self.provider_combo.bind("<<ComboboxSelected>>", self._provider_selection_changed)
        self.verify_button = ttk.Button(sidebar, text="Verify connection", command=self._verify_provider)
        self.verify_button.pack(fill="x", pady=(0, 16))

        self.privacy_button = ttk.Button(sidebar, text="Review privacy", command=self._ack_privacy)
        self.privacy_button.pack(fill="x", pady=4)
        self.permissions_button = ttk.Button(
            sidebar, text="Review permissions", command=self._ack_permissions
        )
        self.permissions_button.pack(fill="x", pady=4)

        ttk.Separator(sidebar).pack(fill="x", pady=18)
        ttk.Label(sidebar, text="Readiness", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        self.readiness_label = ttk.Label(sidebar, text="Checking…", wraplength=190, justify="left")
        self.readiness_label.pack(anchor="w", pady=(6, 18))
        ttk.Label(
            sidebar,
            text="API keys are not saved in onboarding state. Configure credentials using the provider's supported environment or official login.",
            wraplength=190, justify="left"
        ).pack(anchor="w", pady=(0, 14))
        ttk.Button(sidebar, text="Refresh status", command=self._refresh_setup).pack(fill="x")
        
        main = ttk.Frame(self.root, padding=(0, 18, 18, 18))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        ttk.Label(main, text="Conversation", font=("TkDefaultFont", 16, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self.transcript = tk.Text(
            main, wrap="word", state="disabled", padx=14, pady=14,
            font=("TkDefaultFont", 11), relief="flat", borderwidth=1
        )
        self.transcript.grid(row=1, column=0, sticky="nsew")
        self.transcript.tag_configure("user", font=("TkDefaultFont", 11, "bold"))
        self.transcript.tag_configure("assistant", font=("TkDefaultFont", 11))
        self.transcript.tag_configure("system", foreground="#666666")
        self.transcript.configure(background="#fbfbfc")

        composer = ttk.Frame(main, padding=(0, 12, 0, 0))
        composer.grid(row=2, column=0, sticky="ew")
        composer.columnconfigure(0, weight=1)
        self.message_var = tk.StringVar()
        self.message_entry = ttk.Entry(composer, textvariable=self.message_var)
        self.message_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.message_entry.bind("<Return>", lambda _event: self._send_message())
        self.send_button = ttk.Button(composer, text="Send", command=self._send_message)
        self.send_button.grid(row=0, column=1)
        ttk.Label(main, textvariable=self.status_var, foreground="#555555").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        self._append("Welcome to Yuki. Verify a real provider, review privacy and permissions, then start chatting.", "system")

    def _append(self, text: str, role: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", ("You" if role == "user" else "Yuki" if role == "assistant" else "Yuki setup") + "\n", role)
        self.transcript.insert("end", text.strip() + "\n\n")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _run_background(self, label: str, operation) -> None:
        if self.busy:
            return
        self.busy = True
        self.status_var.set(label)
        self.verify_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.privacy_button.configure(state="disabled")
        self.permissions_button.configure(state="disabled")

        def work():
            try:
                self.events.put(("success", operation()))
            except Exception as exc:
                safe = str(exc) if isinstance(exc, DesktopServiceError) else (
                    f"Operation failed ({type(exc).__name__}). Check provider setup and retry."
                )
                self.events.put(("error", safe))
        threading.Thread(target=work, daemon=True).start()

    def _provider_selection_changed(self, _event=None) -> None:
        self.service.invalidate_provider_verification()
        self.send_button.configure(state="disabled")
        self.status_var.set("Provider selection changed. Verify the new provider before chatting.")

    def _verify_provider(self) -> None:
        name = self.provider_var.get().strip()
        if not name:
            messagebox.showwarning("Choose a provider", "Select an implemented provider first.")
            return
        confirmed = messagebox.askyesno(
            "Verify provider",
            "Yuki will send a short connectivity-check prompt to the selected provider. "
            "This may incur a small provider charge. Continue?"
        )
        if not confirmed:
            return
        self._run_background("Checking provider connection…", lambda: self.service.verify_provider(name))

    def _ack_privacy(self) -> None:
        if not messagebox.askyesno(
            "Privacy review",
            "Messages you send may be transmitted to the configured AI provider. Do not enter "
            "passwords, API keys, or sensitive information. Continue?"
        ):
            return
        try:
            self.service.acknowledge_privacy()
            self.status_var.set("Privacy acknowledged. Review permissions next.")
            self._refresh_setup()
        except DesktopServiceError as exc:
            messagebox.showwarning("Setup incomplete", str(exc))

    def _ack_permissions(self) -> None:
        if not messagebox.askyesno(
            "Permission review",
            "This desktop chat only sends text to the configured provider. It does not invoke "
            "registered skills, run shell commands, or make workspace changes. Those capabilities "
            "require separate, explicit authorization in the guarded runtime. Acknowledge?"
        ):
            return
        try:
            self.service.acknowledge_permissions()
            self.status_var.set("Setup ready. You can start your first conversation.")
            self._refresh_setup()
        except DesktopServiceError as exc:
            messagebox.showwarning("Setup incomplete", str(exc))

    def _send_message(self) -> None:
        prompt = self.message_var.get()
        if not prompt.strip():
            return
        self.message_var.set("")
        self._append(prompt, "user")
        self._run_background("Yuki is waiting for the provider…", lambda: self.service.chat(prompt))

    def _refresh_setup(self) -> None:
        state = load_onboarding_state(self.service.state_path)
        completed = state["completed_steps"]
        try:
            report = self.service.readiness()
            providers = report.get("providers", [])
            selected = report.get("selected_provider") or "not selected"
            workspace = report.get("workspace", {})
            self.readiness_label.configure(text=(
                f"Provider: {selected}\n"
                f"Implemented providers: {sum(1 for p in providers if p.get('implemented'))}\n"
                f"Workspace: {'ready' if workspace.get('exists') and workspace.get('writable') else 'check required'}"
            ))
        except Exception:
            self.readiness_label.configure(text="Readiness report unavailable.")
        self.privacy_button.configure(state="normal" if "provider" in completed and "privacy" not in completed else "disabled")
        self.permissions_button.configure(state="normal" if "privacy" in completed and "permissions" not in completed else "disabled")
        self.verify_button.configure(state="normal" if not self.busy else "disabled")
        self.send_button.configure(state="normal" if "permissions" in completed and not self.busy else "disabled")
        if "permissions" not in completed:
            self.status_var.set("Complete provider, privacy, and permission setup to chat.")
        elif not self.service.provider_verified:
            self.status_var.set("Verify the provider again to start a session.")
            self.send_button.configure(state="disabled")
        else:
            self.status_var.set("Ready. Chat does not automatically execute skills or tools.")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self.busy = False
                if kind == "success":
                    if isinstance(payload, dict) and payload.get("status") == "verified":
                        self.status_var.set(payload["message"])
                        self._append(payload["message"], "system")
                    elif isinstance(payload, str):
                        self._append(payload, "assistant")
                        self.status_var.set("Response received.")
                else:
                    self.status_var.set(payload)
                    self._append(payload, "system")
                self._refresh_setup()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)


def main() -> None:
    root = tk.Tk()
    YukiDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
