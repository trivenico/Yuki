"""Native Tkinter desktop shell for Yuki.

All provider/database work runs in DesktopRuntimeWorker. The Tk main thread
only renders UI and polls the worker's result queue.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from core.desktop_worker import DesktopRuntimeWorker


class YukiDesktop:
    def __init__(self, root: tk.Tk, worker: DesktopRuntimeWorker | None = None):
        self.root = root
        self.worker = worker or DesktopRuntimeWorker()
        self.pending: dict[str, str] = {}
        self.conversation_ids: dict[str, str] = {}
        self.ready = False
        self.closing = False

        root.title("Yuki")
        root.geometry("960x680")
        root.minsize(640, 440)

        shell = ttk.Frame(root, padding=12)
        shell.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(shell)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Yuki", font=("Segoe UI", 20, "bold")).pack(side=tk.LEFT)
        self.status = tk.StringVar(value="Starting Yuki…")
        ttk.Label(header, textvariable=self.status).pack(side=tk.RIGHT)
        settings = ttk.Frame(shell)
        settings.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(settings, text="AI provider").pack(side=tk.LEFT)
        self.provider_var = tk.StringVar()
        self.provider_select = ttk.Combobox(
            settings, textvariable=self.provider_var, state="readonly", width=18
        )
        self.provider_select.pack(side=tk.LEFT, padx=(8, 6))
        self.provider_select.bind("<<ComboboxSelected>>", self._select_provider)
        ttk.Button(settings, text="Refresh providers", command=self._providers).pack(side=tk.LEFT)
        ttk.Button(settings, text="Sync status", command=self._sync_status).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(settings, text="Pair device", command=self._sync_pair).pack(side=tk.LEFT, padx=(6, 0))
        self.sync_upload_button = ttk.Button(settings, text="Upload queued", command=self._sync_upload)
        self.sync_upload_button.pack(side=tk.LEFT, padx=(6, 0))
        self.sync_upload_active = False
        self.provider_details = tk.StringVar(value="Provider configuration will appear here.")
        ttk.Label(shell, textvariable=self.provider_details, wraplength=900).pack(fill=tk.X, pady=(4, 0))
        self.sync_details = tk.StringVar(value="Sync is local-only until you configure a hub and pair this device.")
        ttk.Label(shell, textvariable=self.sync_details, wraplength=900).pack(fill=tk.X, pady=(3, 0))
        sync_actions = ttk.Frame(shell)
        sync_actions.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(sync_actions, text="Preview sharing", command=self._sync_preview).pack(side=tk.LEFT)
        ttk.Button(sync_actions, text="Queue tasks", command=lambda: self._sync_queue("tasks")).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(sync_actions, text="Queue chats", command=lambda: self._sync_queue("conversations")).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(sync_actions, text="Queue skills", command=lambda: self._sync_queue("skills")).pack(side=tk.LEFT, padx=(6, 0))
        self.sync_run_button = ttk.Button(sync_actions, text="Sync both ways", command=self._sync_run)
        self.sync_run_button.pack(side=tk.LEFT, padx=(6, 0))
        self.sync_run_active = False
        self.sync_preview = None
        self.provider_names: dict[str, str] = {}
        self.provider_var.set("Loading…")

        self.conversation_var = tk.StringVar()
        self.conversation_select = ttk.Combobox(
            shell, textvariable=self.conversation_var, state="readonly"
        )
        self.conversation_select.pack(fill=tk.X, pady=(8, 0))
        self.conversation_select.bind("<<ComboboxSelected>>", self._switch_conversation)

        self.transcript = tk.Text(shell, wrap=tk.WORD, state=tk.DISABLED,
                                  font=("Segoe UI", 10), padx=12, pady=12)
        self.transcript.pack(fill=tk.BOTH, expand=True, pady=(12, 8))
        self.transcript.tag_configure("user", foreground="#2457a7")
        self.transcript.tag_configure("assistant", foreground="#16734a")
        self.transcript.tag_configure("error", foreground="#b42318")

        composer = ttk.Frame(shell)
        composer.pack(fill=tk.X)
        self.entry = ttk.Entry(composer)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._send)
        self.send_button = ttk.Button(composer, text="Send", command=self._send,
                                      state=tk.DISABLED)
        self.send_button.pack(side=tk.LEFT, padx=(8, 0))
        self.cancel_button = ttk.Button(composer, text="Stop", command=self._cancel,
                                        state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT, padx=(6, 0))
        self.stream_active = False
        self._stream_header_written = False

        actions = ttk.Frame(shell)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="New chat", command=self._new_chat).pack(side=tk.LEFT)
        ttk.Button(actions, text="Refresh history", command=self._refresh).pack(side=tk.LEFT, padx=8)

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        try:
            self.worker.start()
        except Exception as exc:
            self._append("Desktop startup failed: " + type(exc).__name__, "error")
            self.status.set("Startup failed")
            return
        self.root.after(75, self._poll)

    def _append(self, text: str, tag: str = "") -> None:
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, text + "\n\n", tag)
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _submit(self, action: str, **payload) -> None:
        try:
            request_id = self.worker.submit(action, **payload)
            self.pending[request_id] = action
        except Exception as exc:
            self._append("Request failed: " + type(exc).__name__, "error")

    def _send(self, _event=None):
        if not self.ready or self.closing:
            return "break"
        message = self.entry.get().strip()
        if not message:
            return "break"
        self.entry.delete(0, tk.END)
        self._append("You: " + message, "user")
        self.send_button.configure(state=tk.DISABLED)
        self.stream_active = True
        self.cancel_button.configure(state=tk.NORMAL)
        self._submit("stream", message=message)
        return "break"

    def _cancel(self):
        if self.stream_active and not self.closing:
            self.status.set("Stopping response…")
            self._submit("cancel")

    def _append_stream_chunk(self, chunk: str) -> None:
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, chunk, "assistant")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _new_chat(self):
        if self.ready and not self.closing:
            self._submit("new", title="Yuki session")

    def _providers(self):
        if self.ready and not self.closing:
            self._submit("providers")
            self._submit("catalog")

    def _select_provider(self, _event=None):
        name = self.provider_names.get(self.provider_var.get())
        if name and self.ready and not self.closing:
            self.status.set("Switching provider…")
            self.provider_select.configure(state=tk.DISABLED)
            self._submit("select_provider", name=name)

    def _sync_status(self):
        if self.ready and not self.closing:
            self._submit("sync_status")

    def _sync_pair(self):
        if not self.ready or self.closing:
            return
        code = simpledialog.askstring(
            "Pair Yuki device",
            "Enter the one-time code generated on your trusted Oracle Linux hub:",
            show="*", parent=self.root,
        )
        if code and code.strip():
            self._submit("sync_pair", code=code.strip(), device_label="Yuki Windows Desktop")
            self.status.set("Pairing device…")

    def _sync_preview(self):
        if self.ready and not self.closing:
            self.status.set("Preparing sync preview…")
            self._submit("sync_preview")

    def _sync_queue(self, collection):
        if not self.ready or self.closing:
            return
        labels = {"tasks": "task details", "conversations": "conversation messages and titles", "skills": "data-only skill manifests"}
        if not messagebox.askyesno(
            "Queue Yuki data for sync",
            "Queue " + labels[collection] + " on this device? This only adds them to the encrypted-transport outbox; nothing is uploaded until you choose Upload queued or Sync both ways.",
            parent=self.root,
        ):
            return
        self._submit("sync_queue", collection=collection)
        self.status.set("Queueing " + collection + "…")

    def _sync_run(self):
        if not self.ready or self.closing or self.sync_run_active:
            return
        if not messagebox.askyesno(
            "Synchronize Yuki devices",
            "Run a bidirectional sync now? Queued data will be uploaded and supported remote task, conversation and data-only skill events will be applied locally.",
            parent=self.root,
        ):
            return
        self.sync_run_active = True
        self.sync_run_button.configure(state=tk.DISABLED)
        self.status.set("Synchronizing…")
        self._submit("sync_run")

    def _sync_upload(self):
        if self.ready and not self.closing and not self.sync_upload_active:
            self.sync_upload_active = True
            self.sync_upload_button.configure(state=tk.DISABLED)
            self.status.set("Uploading queued events…")
            self._submit("sync_upload")

    def _history(self):
        if self.ready and not self.closing:
            self._submit("history")

    def _refresh(self):
        if self.ready and not self.closing:
            self._submit("list")

    def _switch_conversation(self, _event=None):
        label = self.conversation_var.get()
        conversation_id = self.conversation_ids.get(label)
        if conversation_id and self.ready and not self.closing:
            self._submit("switch", conversation_id=conversation_id)

    def _poll(self):
        if self.closing:
            return
        try:
            while True:
                event = self.worker.results.get_nowait()
                action = event.get("action")
                if action == "ready":
                    if event.get("ok"):
                        self.ready = True
                        self.status.set("Ready")
                        self.send_button.configure(state=tk.NORMAL)
                        self.entry.focus_set()
                        self._submit("providers")
                        self._submit("catalog")
                        self._submit("list")
                        self._submit("sync_status")
                    else:
                        self.status.set("Unavailable")
                        self._append("Yuki could not start (" + event.get("error", "Error") + "). Check the CLI setup and provider configuration.", "error")
                elif action == "chunk":
                    if not self._stream_header_written:
                        self.transcript.configure(state=tk.NORMAL)
                        self.transcript.insert(tk.END, "Yuki: ", "assistant")
                        self.transcript.configure(state=tk.DISABLED)
                        self._stream_header_written = True
                    self._append_stream_chunk(event.get("chunk", ""))
                elif action == "stream":
                    self.stream_active = False
                    self._stream_header_written = False
                    self.send_button.configure(state=tk.NORMAL)
                    self.cancel_button.configure(state=tk.DISABLED)
                    if event.get("cancelled"):
                        self._append("\n[Response stopped. Partial output was not saved as a completed answer.]")
                        self.status.set("Response stopped")
                    elif event.get("ok"):
                        self._append("\n")
                        self.status.set("Ready")
                    else:
                        self._append("\nYuki request failed: " + event.get("error", "Error"), "error")
                        self.status.set("Request failed")
                elif action == "providers" and event.get("ok"):
                    providers = event.get("providers", [])
                    self.provider_names = {
                        f'{item.get("name", "unknown")} — {"implemented" if item.get("implemented") else "planned"}':
                        item.get("name", "")
                        for item in providers
                    }
                    self.provider_select.configure(values=list(self.provider_names))
                    selected = event.get("selected")
                    selected_label = next(
                        (label for label, name in self.provider_names.items() if name == selected),
                        "",
                    )
                    self.provider_var.set(selected_label)
                    self.provider_select.configure(state="readonly")
                    self.status.set("Ready · " + (selected or "no provider"))
                elif action == "select_provider":
                    self.provider_select.configure(state="readonly")
                    if event.get("ok"):
                        self.status.set("Ready · " + event.get("selected", "provider selected"))
                        self._append(event.get("result", "Provider selected."))
                    else:
                        self.status.set("Provider unchanged")
                        self._append(event.get("result", "Provider selection failed."), "error")
                    self._submit("providers")
                    self._submit("catalog")
                elif action == "sync_status":
                    if event.get("ok"):
                        self.sync_details.set(event.get("status", "Sync status unavailable."))
                    else:
                        self.sync_details.set("Sync status unavailable (" + event.get("error", "Error") + ").")
                elif action == "sync_pair":
                    if event.get("ok"):
                        self.sync_details.set(event.get("status", "Device paired."))
                        self.status.set("Device paired")
                        self._append(event.get("result", "Device paired with Yuki Sync Hub."))
                    else:
                        self.status.set("Pairing failed")
                        self._append("Device pairing failed (" + event.get("error", "Error") + "). Check hub URL and OS credential vault.", "error")
                elif action == "sync_preview":
                    if event.get("ok"):
                        self.sync_preview = event.get("result", {})
                        tasks = self.sync_preview.get("tasks", {})
                        chats = self.sync_preview.get("conversations", {})
                        skills = self.sync_preview.get("skills", {})
                        summary = (
                            "Sharing preview — tasks: " + str(tasks.get("total", 0))
                            + "; chats: " + str(chats.get("conversations", 0)) + " / "
                            + str(chats.get("messages", 0)) + " messages; data-only skills: "
                            + str(skills.get("installed_data_only_packs", 0))
                            + ". Nothing queued or uploaded."
                        )
                        self.sync_details.set(summary)
                        self._append(summary)
                        self.status.set("Preview ready")
                    else:
                        self.status.set("Preview failed")
                        self._append("Sync preview failed (" + event.get("error", "Error") + ").", "error")
                elif action == "sync_queue":
                    if event.get("ok"):
                        result = event.get("result", {})
                        queued = sum(value for key, value in result.items()
                                     if key in {"queued", "conversations", "messages"} and isinstance(value, int))
                        self.sync_details.set(event.get("status", "Sync queue updated."))
                        self.status.set("Data queued locally")
                        self._append("Queued " + str(queued) + " item(s) from " + result.get("collection", "selected collection") + ". Nothing uploaded yet.")
                    else:
                        self.status.set("Queue failed")
                        self._append("Unable to queue sync data (" + event.get("error", "Error") + ").", "error")
                elif action == "sync_run":
                    self.sync_run_active = False
                    self.sync_run_button.configure(state=tk.NORMAL)
                    if event.get("ok"):
                        result = event.get("result", {})
                        self.sync_details.set(event.get("status", "Sync status refreshed."))
                        self.status.set("Sync complete")
                        self._append(
                            "Bidirectional sync: " + str(result.get("pushed", 0)) + " uploaded, "
                            + str(result.get("pulled", 0)) + " downloaded/applied, "
                            + str(result.get("conflicts", 0)) + " conflicts."
                        )
                        self._submit("list")
                    else:
                        self.status.set("Sync stopped safely")
                        self._append("Bidirectional sync stopped (" + event.get("error", "Error") + "). No unhandled event was acknowledged.", "error")
                elif action == "sync_upload":
                    self.sync_upload_active = False
                    self.sync_upload_button.configure(state=tk.NORMAL)
                    if event.get("ok"):
                        result = event.get("result", {})
                        self.sync_details.set(event.get("status", "Sync status refreshed."))
                        self.status.set("Upload complete")
                        self._append(
                            "Sync upload: " + str(result.get("pushed", 0)) + " uploaded, "
                            + str(result.get("conflicts", 0)) + " conflicts, "
                            + str(result.get("pending", 0)) + " still pending."
                        )
                    else:
                        self.status.set("Upload failed")
                        self._append("Sync upload failed (" + event.get("error", "Error") + ").", "error")
                elif action == "catalog" and event.get("ok"):
                    selected_name = self.provider_names.get(self.provider_var.get())
                    selected = next(
                        (item for item in event.get("catalog", []) if item.get("name") == selected_name),
                        None,
                    )
                    if selected:
                        model = selected.get("configured_model") or "model not configured"
                        self.provider_details.set(
                            f'Model: {model}  ·  Privacy: {selected.get("privacy", "unknown")}  ·  '
                            f'Readiness: {selected.get("status", "unknown")}  ·  '
                            f'Credential configured: {"yes" if selected.get("credential_configured") else "no"}'
                        )
                    else:
                        self.provider_details.set("No provider is currently selected.")
                elif action == "list" and event.get("ok"):
                    conversations = event.get("conversations", [])
                    self.conversation_ids = {
                        f'{item.get("title", "Yuki session")} · {item.get("conversation_id", "")[-8:]}':
                        item.get("conversation_id", "")
                        for item in conversations
                    }
                    labels = list(self.conversation_ids)
                    self.conversation_select.configure(values=labels)
                    active_id = event.get("active_conversation_id")
                    active_label = next(
                        (label for label, value in self.conversation_ids.items() if value == active_id),
                        "",
                    )
                    self.conversation_var.set(active_label)
                    self._submit("history")
                elif action == "history" and event.get("ok"):
                    self.transcript.configure(state=tk.NORMAL)
                    self.transcript.delete("1.0", tk.END)
                    self.transcript.configure(state=tk.DISABLED)
                    messages = event.get("messages", [])
                    if messages:
                        self._append("— Conversation history —")
                        for item in messages:
                            role = item.get("role", "assistant")
                            self._append(("You: " if role == "user" else "Yuki: ") + item.get("content", ""),
                                         "user" if role == "user" else "assistant")
                elif action == "switch" and event.get("ok"):
                    self.transcript.configure(state=tk.NORMAL)
                    self.transcript.delete("1.0", tk.END)
                    self.transcript.configure(state=tk.DISABLED)
                    self._submit("list")
                elif action == "new" and event.get("ok"):
                    self.transcript.configure(state=tk.NORMAL)
                    self.transcript.delete("1.0", tk.END)
                    self.transcript.configure(state=tk.DISABLED)
                    self.status.set("New conversation")
                    self._submit("list")
                elif action == "switch" and not event.get("ok"):
                    self._append("Could not switch conversation.", "error")
                if event.get("request_id"):
                    self.pending.pop(event["request_id"], None)
        except queue.Empty:
            pass
        self.root.after(75, self._poll)

    def _close(self):
        if self.closing:
            return
        self.closing = True
        self.ready = False
        self.status.set("Closing…")
        try:
            self.worker.submit("close")
        except Exception:
            self.root.destroy()
            return
        self._wait_close()

    def _wait_close(self):
        self.worker.join(0)
        if self.worker._thread.is_alive():
            self.root.after(50, self._wait_close)
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    YukiDesktop(root)
    root.mainloop()


if __name__ == "__main__":
    main()
