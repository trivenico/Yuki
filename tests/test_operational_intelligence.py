from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.operational_intelligence import OperationalIntelligenceCenter
from core.proactive_alerts import ProactiveAlertStore


class FakeTaskRuntime:
    def __init__(self, tasks=None, error=None):
        self.tasks = tasks or []
        self.error = error

    def list_task_summaries(self, *, actor_id, limit):
        if self.error:
            raise self.error
        assert actor_id == "owner-a"
        return self.tasks[:limit]


class FakeWorkerRegistry:
    def __init__(self, workers=None):
        self.workers = workers or []
        self.calls = 0

    def list_workers(self, *, stale_after_seconds, limit):
        self.calls += 1
        return self.workers[:limit]


def test_operational_overview_aggregates_only_bounded_metadata(tmp_path):
    now = datetime.now(timezone.utc)
    tasks = [
        SimpleNamespace(state="queued", lease_expires_at=None),
        SimpleNamespace(state="waiting_approval", lease_expires_at=None),
        SimpleNamespace(state="running", lease_expires_at=(now - timedelta(minutes=2)).isoformat()),
        SimpleNamespace(state="succeeded", lease_expires_at=None),
        SimpleNamespace(state="failed", lease_expires_at=None),
        SimpleNamespace(state="interrupted", lease_expires_at=None),
    ]
    workers = [
        SimpleNamespace(status="active"),
        SimpleNamespace(status="stale"),
        SimpleNamespace(status="stopped"),
    ]
    alerts = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    alerts.observe(
        owner_id="owner-a", category="task.failed", subject="task-1",
        message="Sensitive task payload must not be rendered.", notifications_enabled=False,
    )
    center = OperationalIntelligenceCenter(
        task_runtime=FakeTaskRuntime(tasks),
        worker_registry=FakeWorkerRegistry(workers),
        alert_store=alerts,
        worker_access_for=lambda actor: actor == "owner-a",
    )
    report = center.snapshot(actor_id="owner-a")
    assert report.task_access is True
    assert report.worker_access is True
    assert report.task_counts["total"] == 6
    assert report.task_counts["waiting_approval"] == 1
    assert report.expired_running_leases == 1
    assert report.worker_counts["active"] == 1
    assert report.worker_counts["stale"] == 1
    assert report.alert_counts["active"] == 1
    rendered = report.render()
    assert "Tasks: total=6" in rendered
    assert "Workers: total=3" in rendered
    assert "Sensitive task payload" not in rendered
    assert "No tasks were started" in rendered


def test_operational_overview_fails_closed_for_worker_registry_without_access(tmp_path):
    workers = FakeWorkerRegistry([SimpleNamespace(status="active")])
    alerts = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    center = OperationalIntelligenceCenter(
        task_runtime=FakeTaskRuntime(error=PermissionError("denied")),
        worker_registry=workers,
        alert_store=alerts,
        worker_access_for=lambda _actor: False,
    )
    report = center.snapshot(actor_id="owner-a")
    assert report.task_access is False
    assert report.worker_access is False
    assert report.task_counts == {}
    assert report.worker_counts == {}
    assert workers.calls == 0
    assert "operator health access not granted" in report.render()


def test_operational_overview_worker_authorizer_exception_fails_closed(tmp_path):
    workers = FakeWorkerRegistry([SimpleNamespace(status="active")])
    center = OperationalIntelligenceCenter(
        task_runtime=FakeTaskRuntime(),
        worker_registry=workers,
        alert_store=ProactiveAlertStore(tmp_path / "alerts.sqlite3"),
        worker_access_for=lambda _actor: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    report = center.snapshot(actor_id="owner-a")
    assert report.worker_access is False
    assert workers.calls == 0


def test_operational_overview_validates_bounds_and_actor(tmp_path):
    import pytest
    common = dict(
        task_runtime=FakeTaskRuntime(),
        worker_registry=FakeWorkerRegistry(),
        alert_store=ProactiveAlertStore(tmp_path / "alerts.sqlite3"),
        worker_access_for=lambda _actor: False,
    )
    with pytest.raises(ValueError):
        OperationalIntelligenceCenter(**common, task_limit=0)
    center = OperationalIntelligenceCenter(**common)
    with pytest.raises(ValueError):
        center.snapshot(actor_id="")
