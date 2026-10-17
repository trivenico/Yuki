"""Opt-in local health/habit log. Not a medical device or clinical recommender."""
from __future__ import annotations
import csv, json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

class HealthTracker:
    MAX_ENTRIES=10000
    ALLOWED={"medication","mood","exercise","habit","symptom","sleep","note"}
    def __init__(self,path="yuki_health.json"):
        self.path=Path(path).expanduser().resolve(); self._lock=RLock()
    def _load(self):
        try: data=json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError: return []
        except (OSError,json.JSONDecodeError) as exc: raise RuntimeError("Health log unreadable; no data overwritten.") from exc
        if not isinstance(data,list) or len(data)>self.MAX_ENTRIES or any(not isinstance(x,dict) for x in data): raise RuntimeError("Health log structure invalid.")
        return data
    def _save(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True); fd,temp=tempfile.mkstemp(prefix=".yuki-health-",suffix=".tmp",dir=str(self.path.parent))
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(temp,self.path)
        finally:
            try: os.unlink(temp)
            except FileNotFoundError: pass
    def add(self,kind,detail,*,occurred_at=None):
        if kind not in self.ALLOWED: raise ValueError("Unsupported health entry type.")
        if not isinstance(detail,str) or not detail.strip() or len(detail)>1000: raise ValueError("Detail must be 1-1000 characters.")
        timestamp=occurred_at or datetime.now(timezone.utc)
        if not isinstance(timestamp,datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None: raise ValueError("occurred_at must be timezone-aware.")
        with self._lock:
            rows=self._load()
            if len(rows)>=self.MAX_ENTRIES: raise ValueError("Health log is full.")
            item={"id":uuid4().hex,"type":kind,"detail":detail.strip(),"occurred_at":timestamp.astimezone(timezone.utc).isoformat()}
            rows.append(item); self._save(rows); return dict(item)
    def list(self,kind=None,limit=100):
        if kind is not None and kind not in self.ALLOWED: raise ValueError("Unsupported health entry type.")
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=500: raise ValueError("limit must be 1-500.")
        with self._lock: return [dict(x) for x in self._load() if kind is None or x["type"]==kind][-limit:]
    def export_csv(self,path):
        target=Path(path).expanduser().resolve()
        with self._lock: rows=self._load()
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open("w",encoding="utf-8",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=["id","type","detail","occurred_at"]); writer.writeheader(); writer.writerows(rows)
        return str(target)
