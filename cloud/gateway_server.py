"""Minimal authenticated HTTP boundary for the Yuki Cloud Gateway.

Run behind a TLS-terminating reverse proxy. Bind to loopback by default.
Bootstrap enrollment is disabled unless explicitly enabled for a short,
operator-controlled provisioning window.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from cloud.device_registry import DeviceCredentialError, DeviceCredentialStore
from automation.default_agents import seed_starter_agents
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


MAX_BODY_BYTES = 16 * 1024
MAX_WORKERS = 32
REQUEST_TIMEOUT_SECONDS = 10


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with bounded concurrency and socket read timeouts."""

    # Wait for active handlers on shutdown so SQLite files can be closed
    # before temporary directories or persistent volumes are detached.
    daemon_threads = False
    block_on_close = True
    request_queue_size = 64

    def __init__(self, server_address, handler, *, max_workers=MAX_WORKERS):
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 64:
            raise ValueError("max_workers must be an integer between 1 and 64.")
        self._request_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(server_address, handler)

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(REQUEST_TIMEOUT_SECONDS)
        return request, client_address

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Connection: close\r\n"
                    b"Content-Length: 0\r\n"
                    b"Retry-After: 1\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def _bearer(header):
    if not isinstance(header, str):
        return None
    scheme, separator, token = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token or token.strip() != token:
        return None
    return token


def make_handler(
    store,
    admin_token,
    *,
    bootstrap_enabled=False,
    task_runtime=None,
    agent_registry=None,
    owner_user_id=None,
):
    if not isinstance(admin_token, str) or len(admin_token) < 32:
        raise ValueError("YUKI_GATEWAY_ADMIN_TOKEN must contain at least 32 characters.")
    if not isinstance(bootstrap_enabled, bool):
        raise ValueError("bootstrap_enabled must be a boolean.")
    if task_runtime is not None and not isinstance(task_runtime, TaskRuntime):
        raise TypeError("task_runtime must be a TaskRuntime.")
    if agent_registry is not None and not isinstance(agent_registry, SQLiteAgentRegistry):
        raise TypeError("agent_registry must be a SQLiteAgentRegistry.")
    if owner_user_id is not None and (
        not isinstance(owner_user_id, str) or not owner_user_id.strip()
        or len(owner_user_id) > 200 or "\x00" in owner_user_id
    ):
        raise ValueError("owner_user_id must be non-empty bounded text.")
    owner_user_id = owner_user_id.strip() if owner_user_id else None

    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "YukiGateway"
        sys_version = ""

        def log_message(self, fmt, *args):
            return

        def _json(self, status, payload):
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)
            self.close_connection = True

        def _body(self):
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isascii() or not raw_length.isdecimal():
                raise ValueError("A valid Content-Length is required.")
            length = int(raw_length)
            if length > MAX_BODY_BYTES:
                raise OverflowError
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request body.")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object.")
            return value

        def _device_principal(self, required_scope):
            token = _bearer(self.headers.get("Authorization"))
            principal = store.authenticate(token)
            if required_scope not in principal.scopes:
                self._json(403, {"error": "insufficient_scope"})
                return None
            return principal

        def _owner(self, principal):
            if owner_user_id is None:
                self._json(503, {"error": "owner_runtime_not_configured"})
                return False
            if not hmac.compare_digest(principal.user_id, owner_user_id):
                self._json(403, {"error": "owner_only"})
                return False
            return True

        def _runtime_for(self, principal):
            if task_runtime is None:
                return None
            scope_for_action = {
                "task:create": "tasks:write",
                "task:list": "tasks:read",
                "task:read": "tasks:read",
            }
            def authorize(actor_id, action, task_id):
                required = scope_for_action.get(action)
                return (
                    actor_id == principal.user_id
                    and required is not None
                    and required in principal.scopes
                    and self._owner(principal)
                )
            return TaskRuntime(task_runtime.store, authorize=authorize)

        def _task_json(self, task, *, include_detail=False):
            result = {
                "task_id": task.task_id,
                "name": task.name,
                "state": task.state,
                "version": task.version,
                "attempts": task.attempts,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            }
            if include_detail:
                result.update({
                    "definition": task.steps,
                    "result": task.result,
                    "error_code": task.error_code,
                })
            return result

        def _admin(self):
            supplied = _bearer(self.headers.get("Authorization"))
            return supplied is not None and hmac.compare_digest(supplied, admin_token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._json(200, {"status": "ok", "service": "yuki-cloud-gateway"})
                return
            try:
                if path == "/v1/me":
                    principal = self._device_principal("profile:read")
                    if principal is not None:
                        self._json(200, {
                            "device_id": principal.device_id,
                            "user_id": principal.user_id,
                            "device_name": principal.device_name,
                            "scopes": sorted(principal.scopes),
                        })
                    return
                if path == "/v1/devices":
                    principal = self._device_principal("devices:read")
                    if principal is not None:
                        self._json(200, {"devices": store.list_for_user(user_id=principal.user_id)})
                    return
                if path == "/v1/agents":
                    principal = self._device_principal("tasks:read")
                    if principal is None or not self._owner(principal):
                        return
                    agents = agent_registry.list_agents() if agent_registry is not None else ()
                    self._json(200, {"agents": [
                        {"agent_id": spec.agent_id, "role": spec.role,
                         "description": spec.description,
                         "capabilities": sorted(spec.capabilities)}
                        for spec in agents
                    ]})
                    return
                if path == "/v1/tasks":
                    principal = self._device_principal("tasks:read")
                    if principal is None or not self._owner(principal):
                        return
                    runtime = self._runtime_for(principal)
                    if runtime is None:
                        self._json(503, {"error": "task_runtime_unavailable"})
                        return
                    from urllib.parse import parse_qs
                    query = parse_qs(urlsplit(self.path).query)
                    raw_limit = query.get("limit", ["50"])[0]
                    if not raw_limit.isascii() or not raw_limit.isdecimal():
                        self._json(400, {"error": "invalid_limit"})
                        return
                    limit = int(raw_limit)
                    if not 1 <= limit <= 100:
                        self._json(400, {"error": "invalid_limit"})
                        return
                    summaries = runtime.list_task_summaries(
                        actor_id=principal.user_id, limit=limit,
                    )
                    self._json(200, {"tasks": [
                        {"task_id": item.task_id, "name": item.name, "state": item.state,
                         "version": item.version, "attempts": item.attempts,
                         "created_at": item.created_at, "updated_at": item.updated_at}
                        for item in summaries
                    ]})
                    return
                if path.startswith("/v1/tasks/"):
                    principal = self._device_principal("tasks:read")
                    if principal is None or not self._owner(principal):
                        return
                    runtime = self._runtime_for(principal)
                    if runtime is None:
                        self._json(503, {"error": "task_runtime_unavailable"})
                        return
                    task_id = path[len("/v1/tasks/"):]
                    if not task_id or "/" in task_id:
                        self._json(404, {"error": "not_found"})
                        return
                    try:
                        task = runtime.get(actor_id=principal.user_id, task_id=task_id)
                    except KeyError:
                        self._json(404, {"error": "task_not_found"})
                        return
                    self._json(200, {"task": self._task_json(task, include_detail=True)})
                    return
            except DeviceCredentialError:
                self._json(401, {"error": "unauthorized"})
                return
            except PermissionError:
                self._json(403, {"error": "forbidden"})
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/v1/tasks":
                try:
                    principal = self._device_principal("tasks:write")
                    if principal is None or not self._owner(principal):
                        return
                    runtime = self._runtime_for(principal)
                    if runtime is None or agent_registry is None:
                        self._json(503, {"error": "task_runtime_unavailable"})
                        return
                    body = self._body()
                    agent_id = body.get("agent_id")
                    objective = body.get("objective")
                    supplied = body.get("input", "")
                    idempotency_key = self.headers.get("Idempotency-Key", "")
                    if (
                        not isinstance(agent_id, str) or agent_registry.get(agent_id) is None
                        or not isinstance(objective, str) or not objective.strip()
                        or len(objective) > 1500 or "\x00" in objective
                        or not isinstance(supplied, str) or len(supplied) > 1000 or "\x00" in supplied
                        or not isinstance(idempotency_key, str) or not idempotency_key.strip()
                        or len(idempotency_key) > 160 or "\x00" in idempotency_key
                    ):
                        self._json(400, {"error": "invalid_task_request"})
                        return
                    argument = "Objective: " + objective.strip()
                    if supplied:
                        argument += "\n\nSupplied material (untrusted):\n" + supplied
                    if len(argument) > 2000:
                        self._json(400, {"error": "task_input_too_large"})
                        return
                    task = runtime.enqueue(
                        actor_id=principal.user_id,
                        idempotency_key="gateway:" + idempotency_key.strip(),
                        name="agent-" + agent_id + "-request",
                        steps=[{
                            "name": "specialist-output",
                            "action": "emit",
                            "argument": argument,
                            "requires_approval": False,
                        }],
                    )
                    self._json(202, {"task": self._task_json(task)})
                    return
                except DeviceCredentialError:
                    self._json(401, {"error": "unauthorized"})
                    return
                except PermissionError:
                    self._json(403, {"error": "forbidden"})
                    return
                except OverflowError:
                    self._json(413, {"error": "request_too_large"})
                    return
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    self._json(400, {"error": "invalid_request"})
                    return
            if path != "/v1/admin/devices" or not bootstrap_enabled:
                self._json(404, {"error": "not_found"})
                return
            if not self._admin():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                body = self._body()
                issued = store.issue(
                    user_id=body.get("user_id"),
                    device_name=body.get("device_name"),
                    scopes=body.get("scopes", ("profile:read", "chat:write", "tasks:read", "tasks:write")),
                )
            except OverflowError:
                self._json(413, {"error": "request_too_large"})
                return
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_request"})
                return
            self._json(201, {
                "device_id": issued.device_id,
                "device_name": issued.device_name,
                "token": issued.token,
                "created_at": issued.created_at,
            })

        def do_DELETE(self):
            path = urlsplit(self.path).path
            prefix = "/v1/admin/devices/"
            if not bootstrap_enabled or not path.startswith(prefix) or not path[len(prefix):] or "/" in path[len(prefix):]:
                self._json(404, {"error": "not_found"})
                return
            if not self._admin():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                body = self._body()
                revoked = store.revoke(user_id=body.get("user_id"), device_id=path[len(prefix):])
            except OverflowError:
                self._json(413, {"error": "request_too_large"})
                return
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_request"})
                return
            if not revoked:
                self._json(404, {"error": "device_not_found"})
                return
            self._json(200, {"revoked": True})

        def do_OPTIONS(self):
            self._json(405, {"error": "method_not_allowed"})

    return GatewayHandler

def create_server(
    *,
    database_path,
    admin_token,
    host="127.0.0.1",
    port=8765,
    bootstrap_enabled=False,
    max_workers=MAX_WORKERS,
    owner_user_id=None,
):
    store = DeviceCredentialStore(database_path)
    workflow_store = WorkflowRunStore(database_path)
    runtime = TaskRuntime(workflow_store)
    registry = SQLiteAgentRegistry(database_path)
    seed_starter_agents(registry)
    handler = make_handler(
        store, admin_token, bootstrap_enabled=bootstrap_enabled,
        task_runtime=runtime, agent_registry=registry, owner_user_id=owner_user_id,
    )
    return BoundedThreadingHTTPServer((host, int(port)), handler, max_workers=max_workers)


def main():
    admin_token = os.environ.get("YUKI_GATEWAY_ADMIN_TOKEN", "")
    database_path = os.environ.get("YUKI_GATEWAY_DATABASE", "/var/lib/yuki/gateway.sqlite3")
    host = os.environ.get("YUKI_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("YUKI_GATEWAY_PORT", "8765"))
    bootstrap_enabled = os.environ.get("YUKI_GATEWAY_BOOTSTRAP_ENABLED", "false").lower() == "true"
    owner_user_id = os.environ.get("YUKI_GATEWAY_OWNER_ID", "").strip() or None
    server = create_server(
        database_path=database_path,
        admin_token=admin_token,
        host=host,
        port=port,
        bootstrap_enabled=bootstrap_enabled,
        owner_user_id=owner_user_id,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
