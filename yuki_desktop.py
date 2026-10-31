"""Native Tkinter desktop shell for Yuki.

All provider/database work runs in DesktopRuntimeWorker. The Tk main thread
only renders UI and polls the worker's result queue.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk

from core.desktop_worker import DesktopRuntimeWorker


class YukiDesktop:
    def __init__(self, root: tk.Tk, worker: DesktopRuntimeWorker | None = None):
        self.root = root
        self.worker = worker or DesktopRuntimeWorker()
        self.pending: dict[str, str] = {}
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

        actions = ttk.Frame(shell)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="New chat", command=self._new_chat).pack(side=tk.LEFT)
        ttk.Button(actions, text="Refresh history", command=self._history).pack(side=tk.LEFT, padx=8)

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
        self._submit("send", message=message)
        return "break"

    def _new_chat(self):
        if self.ready and not self.closing:
            self._submit("new", title="Yuki session")

    def _history(self):
        if self.ready and not self.closing:
            self._submit("history")

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
                        self._submit("history")
                    else:
                        self.status.set("Unavailable")
                        self._append("Yuki could not start (" + event.get("error", "Error") + "). Check the CLI setup and provider configuration.", "error")
                elif action == "send":
                    self.send_button.configure(state=tk.NORMAL)
                    if event.get("ok"):
                        self._append("Yuki: " + event.get("response", ""), "assistant")
                    else:
                        self._append("Yuki request failed: " + event.get("error", "Error"), "error")
                elif action == "history" and event.get("ok"):
                    messages = event.get("messages", [])
                    if messages:
                        self._append("— Conversation history —")
                        for item in messages:
                            role = item.get("role", "assistant")
                            self._append(("You: " if role == "user" else "Yuki: ") + item.get("content", ""),
                                         "user" if role == "user" else "assistant")
                elif action == "new" and event.get("ok"):
                    self.transcript.configure(state=tk.NORMAL)
                    self.transcript.delete("1.0", tk.END)
                    self.transcript.configure(state=tk.DISABLED)
                    self.status.set("New conversation")
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
