"""Access-controlled operational health for the security monitoring pipeline."""
from __future__ import annotations
import json, math, sqlite3, time
from core.sqlite_file_security import prepare_private_sqlite_path

class SecurityWorkerHeartbeat:
    """Durable, bounded worker liveness timestamps; no payload or identity data."""
    ROLES=frozenset({"ingestion","alert_sync","alert_dispatch"})
    def __init__(self,database_path,*,clock=time.time):
        if not callable(clock): raise TypeError("heartbeat_clock_required")
        self.database_path=prepare_private_sqlite_path(database_path); self.clock=clock
        with sqlite3.connect(self.database_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS security_worker_heartbeat(
                role TEXT PRIMARY KEY, worker_id TEXT NOT NULL, updated_at REAL NOT NULL)""")
    def beat(self,role,worker_id):
        if role not in self.ROLES or not isinstance(worker_id,str) or not worker_id or len(worker_id)>96 or not worker_id.replace("-","").replace("_","").isalnum():
            raise ValueError("security_worker_heartbeat_invalid")
        now=self.clock()
        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now) or now<0: raise ValueError("security_worker_clock_invalid")
        with sqlite3.connect(self.database_path) as db:
            db.execute("""INSERT INTO security_worker_heartbeat(role,worker_id,updated_at) VALUES(?,?,?)
                ON CONFLICT(role) DO UPDATE SET worker_id=excluded.worker_id,updated_at=excluded.updated_at""",
                (role,worker_id,float(now)))
    def snapshot(self):
        with sqlite3.connect(self.database_path) as db:
            rows=db.execute("SELECT role,updated_at FROM security_worker_heartbeat").fetchall()
        return {role:float(stamp) for role,stamp in rows}

class SecurityOperationsHealth:
    """Aggregates safe counts and freshness; never returns incident or alert payloads."""
    def __init__(self,monitor,outbox,heartbeats,*,authorize_operator,max_heartbeat_age=120,
                 max_pending_alerts=1000,clock=time.time):
        if not callable(authorize_operator) or not callable(clock): raise TypeError("security_health_dependencies_required")
        if type(max_heartbeat_age) is not int or not 1<=max_heartbeat_age<=86400: raise ValueError("heartbeat_age_threshold_invalid")
        if type(max_pending_alerts) is not int or not 1<=max_pending_alerts<=1000000: raise ValueError("pending_alert_threshold_invalid")
        if not hasattr(monitor,"database_path") or not hasattr(outbox,"database_path") or not callable(getattr(heartbeats,"snapshot",None)):
            raise TypeError("security_pipeline_components_required")
        self.monitor,self.outbox,self.heartbeats=monitor,outbox,heartbeats
        self.authorize_operator=authorize_operator; self.max_heartbeat_age=max_heartbeat_age
        self.max_pending_alerts=max_pending_alerts; self.clock=clock
    def snapshot(self,operator):
        if not isinstance(operator,str) or not operator or len(operator)>256: raise PermissionError("security_health_operator_invalid")
        try: allowed=self.authorize_operator(operator)
        except Exception as exc: raise PermissionError("security_health_authorization_unavailable") from exc
        if allowed is not True: raise PermissionError("security_health_forbidden")
        now=self.clock()
        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now): raise ValueError("security_health_clock_invalid")
        with sqlite3.connect(self.monitor.database_path) as db:
            event_count,last_event=db.execute("SELECT COUNT(*),MAX(occurred_at) FROM security_events").fetchone()
            open_incidents=db.execute("SELECT COUNT(*) FROM security_incidents WHERE status IN ('open','acknowledged')").fetchone()[0]
        with sqlite3.connect(self.outbox.database_path) as db:
            rows=db.execute("""SELECT state,COUNT(*),MIN(created_at),MAX(attempts),MAX(delivered_at)
                FROM security_alert_outbox GROUP BY state""").fetchall()
        counts={state:0 for state in ("pending","inflight","delivered","dead")}
        oldest_pending=None; max_attempts=0; last_delivery=None
        for state,count,oldest,attempts,delivered in rows:
            counts[state]=int(count)
            if state in {"pending","inflight"} and oldest is not None:
                oldest_pending=float(oldest) if oldest_pending is None else min(oldest_pending,float(oldest))
            max_attempts=max(max_attempts,int(attempts or 0))
            if delivered is not None: last_delivery=float(delivered) if last_delivery is None else max(last_delivery,float(delivered))
        beats=self.heartbeats.snapshot()
        worker_health={}
        for role in sorted(SecurityWorkerHeartbeat.ROLES):
            stamp=beats.get(role)
            age=None if stamp is None else max(0.0,float(now)-stamp)
            worker_health[role]={"healthy":age is not None and age<=self.max_heartbeat_age,"age_seconds":age}
        pending=counts["pending"]+counts["inflight"]
        checks={
            "alert_backlog":pending<=self.max_pending_alerts,
            "dead_letters":counts["dead"]==0,
            "worker_heartbeats":all(v["healthy"] for v in worker_health.values()),
            "monitor_ingestion":last_event is not None and max(0.0,float(now)-float(last_event))<=self.max_heartbeat_age,
        }
        return {"status":"healthy" if all(checks.values()) else "unhealthy",
            "checks":checks,"monitor":{"event_count":int(event_count),"open_incidents":int(open_incidents),
            "last_event_age_seconds":None if last_event is None else max(0.0,float(now)-float(last_event))},
            "outbox":{"pending":counts["pending"],"inflight":counts["inflight"],"delivered":counts["delivered"],
            "dead":counts["dead"],"oldest_pending_age_seconds":None if oldest_pending is None else max(0.0,float(now)-oldest_pending),
            "max_attempts":max_attempts,"last_delivery_age_seconds":None if last_delivery is None else max(0.0,float(now)-last_delivery)},
            "workers":worker_health}
        
class SecurityOperationsHealthASGI:
    """Mount only on a private health listener; requires host authentication per request."""
    def __init__(self,health,*,authenticate_operator):
        if not isinstance(health,SecurityOperationsHealth) or not callable(authenticate_operator):
            raise TypeError("security_health_asgi_dependencies_required")
        self.health=health; self.authenticate_operator=authenticate_operator
    async def __call__(self,scope,receive,send):
        async def respond(status,payload):
            body=json.dumps(payload,separators=(",",":"),allow_nan=False).encode()
            await send({"type":"http.response.start","status":status,"headers":[
                (b"content-type",b"application/json; charset=utf-8"),(b"cache-control",b"no-store"),
                (b"pragma",b"no-cache"),(b"x-content-type-options",b"nosniff"),
                (b"content-length",str(len(body)).encode())]})
            await send({"type":"http.response.body","body":body})
        if scope.get("type")!="http" or scope.get("path")!="/health/security-operations":
            await respond(404,{"status":"not_found"}); return
        if scope.get("method")!="GET":
            await respond(405,{"status":"method_not_allowed"}); return
        try:
            operator=self.authenticate_operator(scope)
            result=self.health.snapshot(operator)
        except PermissionError:
            await respond(403,{"status":"forbidden"}); return
        except Exception:
            await respond(503,{"status":"unavailable"}); return
        await respond(200 if result["status"]=="healthy" else 503,result)
