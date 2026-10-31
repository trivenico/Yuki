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
from core.sync_runtime import SyncRuntime


class DesktopRuntimeWorker:
    def __init__(self, runtime_factory: Callable = YukiApplicationRuntime.create,
                 sync_runtime_factory: Callable = SyncRuntime):
        self._runtime_factory = runtime_factory
        self._sync_runtime_factory = sync_runtime_factory
        self._commands: queue.Queue = queue.Queue(maxsize=64)
        self.results: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="YukiRuntimeWorker", daemon=True
        )
        self._started = False
        self._closing = False
        self._cancel_event = threading.Event()
        self._active_request_id = None

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Desktop runtime worker has already been started.")
        self._started = True
        self._thread.start()

    def submit(self, action: str, **payload) -> str:
        if not self._started or self._closing:
            raise RuntimeError("Desktop runtime worker is not accepting commands.")
        if action == "cancel":
            if self._active_request_id is not None:
                self._cancel_event.set()
            return uuid.uuid4().hex
        if action not in {"send", "stream", "history", "new", "list", "switch", "providers", "catalog", "select_provider", "sync_status", "sync_pair", "close"}:
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
        sync_runtime = None
        try:
            runtime = self._runtime_factory()
            sync_runtime = self._sync_runtime_factory()
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
                elif action == "stream":
                    self._cancel_event.clear()
                    self._active_request_id = request_id
                    chunks = []
                    stream = runtime.stream_message(payload.get("message", ""))
                    try:
                        for chunk in stream:
                            if self._cancel_event.is_set():
                                break
                            chunks.append(chunk)
                            self._emit(request_id, "chunk", chunk=chunk)
                        self._emit(request_id, action, ok=not self._cancel_event.is_set(),
                                   cancelled=self._cancel_event.is_set(),
                                   response="".join(chunks))
                    finally:
                        stream.close()
                        self._active_request_id = None
                        self._cancel_event.clear()
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
                elif action == "providers":
                    current = runtime.providers.current()
                    self._emit(request_id, action, ok=True,
                               providers=runtime.providers.list_providers(),
                               selected=current.name if current else None)
                elif action == "catalog":
                    self._emit(request_id, action, ok=True,
                               catalog=runtime.providers.catalog())
                elif action == "sync_status":
                    self._emit(request_id, action, ok=True, status=sync_runtime.status())
                elif action == "sync_pair":
                    result = sync_runtime.pair(payload.get("code", ""),
                                               payload.get("device_label", "Yuki Desktop"))
                    self._emit(request_id, action, ok=True, result=result,
                               status=sync_runtime.status())
                elif action == "select_provider":
                    name = payload.get("name", "")
                    result = runtime.providers.select(name)
                    if result.startswith("Selected provider:"):
                        adapter = runtime.providers.create_selected_adapter()
                        if adapter is None:
                            raise RuntimeError("Selected provider adapter is unavailable.")
                        runtime.agent.set_provider(adapter)
                    self._emit(request_id, action, ok=result.startswith("Selected provider:"),
                               result=result, selected=runtime.providers.current().name
                               if runtime.providers.current() else None)
                elif action == "close":
                    try:
                        if sync_runtime is not None:
                            sync_runtime.close()
                        runtime.close()
                        self._emit(request_id, action, ok=True)
                    except Exception as exc:
                        self._emit(request_id, action, ok=False, error=type(exc).__name__)
                    return
            except Exception as exc:
                self._emit(request_id, action, ok=False, error=type(exc).__name__)
        if sync_runtime is not None:
            sync_runtime.close()
        if runtime is not None:
            runtime.close()
