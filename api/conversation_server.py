"""Loopback-only authenticated HTTP API for Yuki conversations."""
from __future__ import annotations
import json, os, re, secrets, sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from itertools import chain
from automation.agent_sessions import AgentSessionStore
from automation.conversation_runtime import ConversationGenerationError, ConversationRuntime
from automation.conversation_service import ConversationService
from core.auth import AuthenticationError, BuiltInAuthService

MAX_BODY = 40000
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

def build_application(data_dir, provider_manager):
    root = Path(data_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    try: root.chmod(0o700)
    except OSError: pass
    auth = BuiltInAuthService(str(root / "auth.sqlite3"))
    sessions = AgentSessionStore(root / "conversations.sqlite3",
        authorize=lambda actor, action, resource: bool(actor))
    conversations = ConversationService(sessions)
    runtime = ConversationRuntime(conversations)

    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "YukiLocalAPI/1", ""
        def log_message(self, *_args): pass
        def send_json(self, status, value):
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(raw)
        def body(self):
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or not 1 <= int(length) <= MAX_BODY:
                raise ValueError("invalid body length")
            value = json.loads(self.rfile.read(int(length)).decode("utf-8"))
            if not isinstance(value, dict): raise ValueError("JSON object required")
            return value
        def principal(self):
            scheme, _, token = self.headers.get("Authorization", "").partition(" ")
            return auth.resolve_session(token) if scheme.lower() == "bearer" else None
        def require(self):
            user = self.principal()
            if user is None: self.send_json(401, {"error":"authentication_required"})
            return user
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health": return self.send_json(200, {"status":"ok"})
            user = self.require()
            if user is None: return
            if path == "/v1/me":
                return self.send_json(200, {"user_id":user.user_id,"username":user.username,
                    "role":user.role,"permissions":sorted(user.permissions)})
            if path == "/v1/conversations":
                items = conversations.list_sessions(actor_id=user.user_id, limit=50)
                return self.send_json(200, {"items":[{"session_id":x.session_id,"title":x.title,
                    "agent_id":x.agent_id,"model_id":x.model_id,"state":x.state,
                    "version":x.version,"updated_at":x.updated_at} for x in items]})
            match = re.fullmatch(r"/v1/conversations/([^/]+)/events", path)
            if match and SESSION_ID.fullmatch(match.group(1)):
                try:
                    after = int(self.headers.get("X-After-Sequence", "0"))
                    events = conversations.history(actor_id=user.user_id,session_id=match.group(1),
                        after_sequence=after,limit=100)
                except (ValueError, KeyError): return self.send_json(404, {"error":"not_found"})
                return self.send_json(200, {"items":[{"sequence":e.sequence,"event_id":e.event_id,
                    "kind":e.kind,"payload":e.payload,"created_at":e.created_at} for e in events]})
            return self.send_json(404, {"error":"not_found"})
        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/v1/auth/login":
                try:
                    data = self.body()
                    issued = auth.authenticate(data.get("username"), data.get("password"))
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    return self.send_json(400, {"error":"invalid_request"})
                except AuthenticationError:
                    return self.send_json(401, {"error":"invalid_credentials"})
                return self.send_json(200, {"access_token":issued.token,"token_type":"Bearer",
                    "expires_at":issued.expires_at,"user":{"user_id":issued.user.user_id,
                    "username":issued.user.username,"role":issued.user.role}})
            user = self.require()
            if user is None: return
            if path == "/v1/auth/logout":
                _, _, token = self.headers.get("Authorization","").partition(" ")
                auth.revoke_session(token)
                return self.send_json(200, {"status":"signed_out"})
            if path == "/v1/conversations":
                try:
                    data = self.body()
                    title = data.get("title","Yuki conversation")
                    if not isinstance(title,str) or not title.strip() or len(title)>160: raise ValueError()
                    item = conversations.start(actor_id=user.user_id,
                        idempotency_key=self.headers.get("Idempotency-Key") or secrets.token_urlsafe(18),
                        title=title.strip(),agent_id="general",
                        model_id=data.get("model_id") if isinstance(data.get("model_id"),str) else None)
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    return self.send_json(400, {"error":"invalid_request"})
                return self.send_json(201, {"session_id":item.session_id,"title":item.title,
                    "state":item.state,"version":item.version})
            match = re.fullmatch(r"/v1/conversations/([^/]+)/messages",path)
            if match and SESSION_ID.fullmatch(match.group(1)):
                try:
                    data = self.body()
                    content = data.get("content")
                    key = self.headers.get("Idempotency-Key","").strip()
                    if not isinstance(content,str) or not content.strip() or len(content)>32000:
                        raise ValueError()
                    if not key or len(key)>160:
                        return self.send_json(400, {"error":"idempotency_key_required"})
                    events = runtime.respond_stream_with_manager(actor_id=user.user_id,
                        session_id=match.group(1),idempotency_key=key,user_message=content,
                        provider_manager=provider_manager,allow_cloud=False)
                    # Prime before committing headers so immediate failures return HTTP status.
                    first = next(events, None)
                    self.send_response(200)
                    self.send_header("Content-Type","text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control","no-cache, no-store")
                    self.send_header("X-Accel-Buffering","no")
                    self.send_header("X-Content-Type-Options","nosniff")
                    self.end_headers()
                    try:
                        for event in chain((first,) if first is not None else (), events):
                            payload = json.dumps({"type":event.type,"content":event.content},
                                ensure_ascii=False,separators=(",",":")).encode()
                            self.wfile.write(b"data: "+payload+b"\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError,OSError):
                        pass
                    except ConversationGenerationError:
                        error_event = json.dumps({"type":"error","content":"provider_generation_failed"}).encode()
                        try:
                            self.wfile.write(b"data: "+error_event+b"\n\n")
                            self.wfile.flush()
                        except OSError:
                            pass
                    finally: events.close()
                    return
                except ConversationGenerationError:
                    return self.send_json(502, {"error":"provider_generation_failed"})
                except (ValueError,TypeError,KeyError,PermissionError):
                    return self.send_json(400, {"error":"conversation_request_rejected"})
            return self.send_json(404, {"error":"not_found"})
        def do_OPTIONS(self): self.send_json(405, {"error":"method_not_allowed"})
    return auth, Handler

def serve(provider_manager, host="127.0.0.1", port=8765, data_dir=None):
    if host not in {"127.0.0.1","::1","localhost"}:
        raise ValueError("Yuki API binds to loopback only; use a TLS reverse proxy for remote access.")
    if isinstance(port,bool) or not isinstance(port,int) or not 0<=port<=65535:
        raise ValueError("invalid port")
    if data_dir is None: data_dir = Path(os.environ.get("YUKI_DATA_DIR",Path.home()/".yuki"))
    auth, handler = build_application(data_dir,provider_manager)
    if not auth.list_users():
        username,password = os.environ.get("YUKI_BOOTSTRAP_ADMIN"),os.environ.get("YUKI_BOOTSTRAP_PASSWORD")
        if username and password:
            auth.bootstrap_admin(username,password)
            print("Yuki initial administrator created.",file=sys.stderr)
        else:
            print("Set YUKI_BOOTSTRAP_ADMIN and YUKI_BOOTSTRAP_PASSWORD for first startup.",file=sys.stderr)
    server = ThreadingHTTPServer((host,port),handler)
    print("Yuki API listening on http://%s:%s"%(host,server.server_port),file=sys.stderr)
    try: server.serve_forever()
    finally: server.server_close()
