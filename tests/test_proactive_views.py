from datetime import datetime, timezone

from core.proactive_alerts import ProactiveAlertStore
from ui.proactive_views import render_notification_history, render_proactive_alerts


def test_alert_view_renders_owner_scoped_alerts_without_payloads(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    store.observe(
        owner_id="owner-a", category="task.failed", subject="task-7",
        message="Task failed and needs review.", notifications_enabled=False,
        now=datetime(2026, 11, 23, 12, tzinfo=timezone.utc),
    )
    store.observe(
        owner_id="owner-b", category="task.failed", subject="task-secret",
        message="Private owner B detail.", notifications_enabled=False,
    )
    rendered = render_proactive_alerts(store, actor_id="owner-a")
    assert "task-7" in rendered
    assert "Task failed and needs review." in rendered
    assert "task-secret" not in rendered
    assert "Private owner B" not in rendered


def test_notification_history_view_displays_safe_delivery_metadata(tmp_path):
    store = ProactiveAlertStore(tmp_path / "alerts.sqlite3")
    store.observe(
        owner_id="owner-a", category="task.failed", subject="task-7",
        message="Task failed.", notifications_enabled=True,
    )
    rendered = render_notification_history(store, actor_id="owner-a")
    assert "pending" in rendered
    assert "task-7" in rendered
    assert "lease_token" not in rendered


def test_proactive_views_validate_actor_and_store_contract():
    import pytest
    with pytest.raises(ValueError):
        render_proactive_alerts(object(), actor_id="")
    with pytest.raises(TypeError):
        render_notification_history(object(), actor_id="owner-a")


def test_dashboard_notification_preference_commands_have_explicit_confirmation():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "ui" / "dashboard.py").read_text()
    assert 'command == "/alerts"' in source
    assert 'command == "/notifications" or command.startswith("/notifications ")' in source
    assert 'command == "/notify dispatch CONFIRM"' in source
    assert 'notifications_enabled=(parts[1] == "on")' in source

def test_dashboard_alert_commands_require_explicit_confirmation_and_version():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "ui" / "dashboard.py").read_text()
    assert 'command.startswith("/alerts acknowledge ")' in source
    assert 'command.startswith("/alerts resolve ")' in source
    assert 'confirmation = "ACKNOWLEDGE" if action == "acknowledge" else "RESOLVE"' in source
    assert "expected_version=version" in source
