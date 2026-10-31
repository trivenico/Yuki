"""Thread-confined runtime worker for the native Yuki desktop shell.

The worker creates and owns the SQLite-backed application runtime on its own
thread. UI threads exchange plain dictionaries through queues; they never
touch SQLite connections or provider adapters directly.
"""
from __future__ import annotations

import queue
import threading
import uuid
from typing import Callable

from core.application_runtime import YukiApplicationRuntime


class DesktopRuntimeWorker:
    def __init__(self, runtime_factory: Callable = YukiApplicationRuntime.create):
        self._runtime_factory = runtime_factory
        self._commands: queue.Queue = queue.Queue(maxsize=64)
        self.results: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="YukiRuntimeWorker", daemon=True
        )
        self._started = False
        self._closing = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Desktop runtime worker has already been started.")
        self._started = True
        self._thread.start()

    def submit(self, action: str, **payload) -> str:
        if not self._started or self._closing:
            raise RuntimeError("Desktop runtime worker is not accepting commands.")
        if action not in {"send", "history", "new", "list", "switch", "close"}:
            raise ValueError("Unsupported desktop runtime action.")
        request_id = uuid.uuid4().hex
        command = {"request_id": request_id, "action": action, "payload": payload}
        try:
            self._commands.put_nowait(command)
        except queue.Full as exc:
            raise RuntimeError("Yuki is busy; please retry shortly.") from exc
        if action == "close":
            self._closing = True
        return request_id

    def join(self, timeout: float | None = None) -> None:
        if self._started:
            self._thread.join(timeout)

    def _emit(self, request_id: str | None, action: str, **values) -> None:
        self.results.put({"request_id": request_id, "action": action, **values})

    def _run(self) -> None:
        runtime = None
        try:
            runtime = self._runtime_factory()
            self._emit(None, "ready", ok=True)
        except Exception as exc:
            self._emit(None, "ready", ok=False, error=type(exc).__name__)
            return

        while True:
            command = self._commands.get()
            request_id = command["request_id"]
            action = command["action"]
            payload = command["payload"]
            try:
                if action == "send":
                    response = runtime.send_message(payload.get("message", ""))
                    self._emit(request_id, action, ok=True, response=response)
                elif action == "history":
                    self._emit(request_id, action, ok=True, messages=runtime.history())
                elif action == "new":
                    conversation_id = runtime.new_conversation(
                        payload.get("title", "Yuki session")
                    )
                    self._emit(request_id, action, ok=True,
                               conversation_id=conversation_id)
                elif action == "list":
                    self._emit(request_id, action, ok=True,
                               conversations=runtime.conversations.list_conversations(),
                               active_conversation_id=runtime.active_conversation_id)
                elif action == "switch":
                    runtime.switch_conversation(payload.get("conversation_id", ""))
                    self._emit(request_id, action, ok=True,
                               conversation_id=runtime.active_conversation_id)
                elif action == "close":
                    try:
                        runtime.close()
                        self._emit(request_id, action, ok=True)
                    except Exception as exc:
                        self._emit(request_id, action, ok=False, error=type(exc).__name__)
                    return
            except Exception as exc:
                self._emit(request_id, action, ok=False, error=type(exc).__name__)
        if runtime is not None:
            runtime.close()
