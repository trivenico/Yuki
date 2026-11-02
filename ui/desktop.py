"""Cross-platform Tk desktop client for Yuki's authenticated local API.

The access token stays in process memory and is never written to disk.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import secrets
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class YukiDesktopClient:
    def __init__(self, base_url="http://127.0.0.1:8765", timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = None

    def _request(self, method, path, payload=None, *, idempotency_key=None):
        headers = {"Accept": "application/json"}
        data = None
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise RuntimeError("Yuki API response exceeded the client limit.")
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            if exc.code == 401:
                self.token = None
                raise PermissionError("Sign-in expired or credentials were rejected.") from exc
            raise RuntimeError("Yuki API request failed (HTTP %s)." % exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Cannot reach Yuki API at %s." % self.base_url) from exc

    def login(self, username, password):
        result = self._request("POST", "/v1/auth/login",
                               {"username": username, "password": password})
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Yuki API returned an invalid login response.")
        self.token = token
        return result.get("user", {})

    def logout(self):
        if self.token:
            try:
                self._request("POST", "/v1/auth/logout", {})
            finally:
                self.token = None

    def conversations(self):
        return self._request("GET", "/v1/conversations").get("items", [])

    def create_conversation(self, title="Yuki conversation"):
        return self._request("POST", "/v1/conversations", {"title": title},
                              idempotency_key="desktop-session-" + secrets.token_urlsafe(18))

    def history(self, session_id):
        result = self._request("GET", "/v1/conversations/%s/events" % session_id)
        return result.get("items", [])

    def stream_message(self, session_id, content, on_event):
        if not self.token:
            raise PermissionError("Sign in before sending a message.")
        payload = json.dumps({"content": content}).encode("utf-8")
        request = Request(
            self.base_url + "/v1/conversations/%s/messages" % session_id,
            data=payload,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json",
                     "Accept": "text/event-stream",
                     "Idempotency-Key": "desktop-turn-" + secrets.token_urlsafe(18)},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError("Yuki API returned malformed stream data.") from exc
                    if not isinstance(event, dict):
                        raise RuntimeError("Yuki API returned an invalid stream event.")
                    on_event(event)
        except HTTPError as exc:
            if exc.code == 401:
                self.token = None
                raise PermissionError("Sign-in expired. Please sign in again.") from exc
            raise RuntimeError("Yuki message request failed (HTTP %s)." % exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Yuki message stream disconnected.") from exc


def run_desktop(provider_manager, *, data_dir=None, host="127.0.0.1", port=8765):
    """Launch desktop UI and start a local API if the configured port is free."""
    try:
        import tkinter as tk
        from tkinter import messagebox, simpledialog, ttk
    except ImportError as exc:
        raise RuntimeError("Tk is unavailable. Install your platform's Python Tk package.") from exc

    from http.server import ThreadingHTTPServer
    from api.conversation_server import build_application

    if data_dir is None:
        data_dir = Path(os.environ.get("YUKI_DATA_DIR", Path.home() / ".yuki"))
    auth, handler = build_application(data_dir, provider_manager)
    if not auth.list_users():
        username = os.environ.get("YUKI_BOOTSTRAP_ADMIN")
        password = os.environ.get("YUKI_BOOTSTRAP_PASSWORD")
        if username and password:
            auth.bootstrap_admin(username, password)

    server = None
    server_thread = None
    try:
        server = ThreadingHTTPServer((host, port), handler)
        server.daemon_threads = True
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
    except OSError:
        # An already-running local Yuki API is a valid target; never kill it.
        server = None

    client = YukiDesktopClient("http://%s:%s" % (host, port))
    root = tk.Tk()
    root.title("Yuki AI")
    root.geometry("1040x720")
    root.minsize(760, 520)
    events = queue.Queue()
    state = {"session_id": None, "busy": False, "user": None}

    shell = ttk.Frame(root, padding=12)
    shell.pack(fill="both", expand=True)
    login = ttk.Frame(shell)
    login.pack(fill="x")
    ttk.Label(login, text="Yuki AI", font=("TkDefaultFont", 20, "bold")).pack(side="left")
    username_var, password_var = tk.StringVar(), tk.StringVar()
    ttk.Label(login, text="Username").pack(side="left", padx=(24, 4))
    username_entry = ttk.Entry(login, textvariable=username_var, width=18)
    username_entry.pack(side="left")
    ttk.Label(login, text="Password").pack(side="left", padx=(10, 4))
    password_entry = ttk.Entry(login, textvariable=password_var, show="•", width=20)
    password_entry.pack(side="left")
    signin_button = ttk.Button(login, text="Sign in")
    signin_button.pack(side="left", padx=8)
    identity = ttk.Label(login, text="Not signed in")
    identity.pack(side="right")

    body = ttk.Panedwindow(shell, orient="horizontal")
    body.pack(fill="both", expand=True, pady=(12, 0))
    sidebar = ttk.Frame(body, width=250, padding=(0, 0, 10, 0))
    main = ttk.Frame(body)
    body.add(sidebar, weight=1)
    body.add(main, weight=4)
    ttk.Label(sidebar, text="Conversations", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
    conversation_list = tk.Listbox(sidebar, exportselection=False)
    conversation_list.pack(fill="both", expand=True, pady=8)
    new_button = ttk.Button(sidebar, text="＋ New conversation")
    new_button.pack(fill="x")
    transcript = tk.Text(main, wrap="word", state="disabled", padx=12, pady=12)
    transcript.pack(fill="both", expand=True)
    composer = ttk.Frame(main)
    composer.pack(fill="x", pady=(10, 0))
    message_var = tk.StringVar()
    message_entry = ttk.Entry(composer, textvariable=message_var)
    message_entry.pack(side="left", fill="x", expand=True)
    send_button = ttk.Button(composer, text="Send")
    send_button.pack(side="left", padx=(8, 0))
    status = ttk.Label(shell, text="Local API: http://%s:%s" % (host, port))
    status.pack(anchor="w", pady=(8, 0))

    sessions = []

    def append_text(value):
        transcript.configure(state="normal")
        transcript.insert("end", value)
        transcript.see("end")
        transcript.configure(state="disabled")

    def replace_transcript(text):
        transcript.configure(state="normal")
        transcript.delete("1.0", "end")
        transcript.insert("end", text)
        transcript.see("end")
        transcript.configure(state="disabled")

    def background(fn, success):
        def work():
            try:
                result = fn()
                events.put((success, result, None))
            except Exception as exc:
                events.put((success, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def refresh_sessions():
        def done(items, error):
            if error:
                status.configure(text=error)
                return
            sessions[:] = items
            conversation_list.delete(0, "end")
            for item in sessions:
                conversation_list.insert("end", item.get("title") or "Yuki conversation")
            if sessions and state["session_id"] is None:
                select_session(0)
        background(client.conversations, refresh_sessions_done)
    def refresh_sessions_done(items, error):
        if error:
            status.configure(text=error)
            return
        sessions[:] = items
        conversation_list.delete(0, "end")
        for item in sessions:
            conversation_list.insert("end", item.get("title") or "Yuki conversation")
        if sessions and state["session_id"] is None:
            select_session(0)

    def select_session(index):
        if index < 0 or index >= len(sessions):
            return
        item = sessions[index]
        state["session_id"] = item["session_id"]
        conversation_list.selection_clear(0, "end")
        conversation_list.selection_set(index)
        status.configure(text="Loading conversation…")
        def loaded(history, error):
            if error:
                status.configure(text=error)
                return
            lines = []
            for event in history:
                payload = event.get("payload", {})
                if event.get("kind") == "message.user":
                    lines.append("You\n" + str(payload.get("content", "")) + "\n\n")
                elif event.get("kind") == "message.assistant":
                    lines.append("Yuki\n" + str(payload.get("content", "")) + "\n\n")
            replace_transcript("".join(lines))
            status.configure(text="Conversation ready")
        background(lambda: client.history(item["session_id"]), loaded)

    def on_select(_event):
        selection = conversation_list.curselection()
        if selection:
            select_session(selection[0])
    conversation_list.bind("<<ListboxSelect>>", on_select)

    def create_session():
        if state["busy"]:
            return
        def created(item, error):
            if error:
                status.configure(text=error)
                return
            state["session_id"] = item["session_id"]
            replace_transcript("")
            refresh_sessions()
            status.configure(text="New conversation ready")
        background(client.create_conversation, created)
    new_button.configure(command=create_session)

    def send():
        content = message_var.get().strip()
        if not content or state["busy"] or not state["session_id"]:
            return
        message_var.set("")
        state["busy"] = True
        session_id = state["session_id"]
        send_button.configure(state="disabled")
        message_entry.configure(state="disabled")
        new_button.configure(state="disabled")
        conversation_list.configure(state="disabled")
        append_text("You\n" + content + "\n\nYuki\n")
        def got_event(event):
            events.put(("stream_event", event, None))
        def finished(_result=None, error=None):
            state["busy"] = False
            send_button.configure(state="normal")
            message_entry.configure(state="normal")
            new_button.configure(state="normal")
            conversation_list.configure(state="normal")
            if error:
                append_text("\n[Error] " + error + "\n")
                status.configure(text="Generation failed")
            else:
                append_text("\n\n")
                status.configure(text="Ready")
            refresh_sessions()
        background(lambda: client.stream_message(session_id, content, got_event), finished)

    send_button.configure(command=send)
    message_entry.bind("<Return>", lambda _event: send())

    def signin():
        if not username_var.get().strip() or not password_var.get():
            status.configure(text="Enter username and password.")
            return
        signin_button.configure(state="disabled")
        def signed_in(user, error):
            signin_button.configure(state="normal")
            password_var.set("")
            if error:
                status.configure(text=error)
                return
            state["user"] = user
            identity.configure(text=user.get("username", "Signed in"))
            status.configure(text="Signed in · local API connected")
            username_entry.configure(state="disabled")
            password_entry.configure(state="disabled")
            signin_button.configure(state="disabled")
            new_button.configure(state="normal")
            send_button.configure(state="normal")
            message_entry.configure(state="normal")
            refresh_sessions()
        background(lambda: client.login(username_var.get().strip(), password_var.get()), signed_in)
    signin_button.configure(command=signin)
    for widget in (new_button, send_button, message_entry):
        widget.configure(state="disabled")

    def pump_events():
        try:
            while True:
                kind, value, error = events.get_nowait()
                if kind == "stream_event":
                    if value.get("type") == "delta":
                        append_text(value.get("content", ""))
                    elif value.get("type") == "error":
                        status.configure(text="Generation failed")
                else:
                    kind(value, error)
        except queue.Empty:
            pass
        if root.winfo_exists():
            root.after(40, pump_events)

    def close():
        try:
            client.logout()
        except Exception:
            pass
        if server is not None:
            server.shutdown()
            server.server_close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    pump_events()
    if not auth.list_users() and not (os.environ.get("YUKI_BOOTSTRAP_ADMIN") and os.environ.get("YUKI_BOOTSTRAP_PASSWORD")):
        status.configure(text="First run: set YUKI_BOOTSTRAP_ADMIN and YUKI_BOOTSTRAP_PASSWORD, then restart.")
    root.mainloop()
