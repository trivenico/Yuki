from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.operational_summaries import build_operational_summary


def test_daily_summary_counts_allowlisted_events_and_redacts_payloads():
    now = datetime(2026, 11, 23, 8, tzinfo=timezone.utc)
    tasks = [
        SimpleNamespace(state="running", result={"secret": "never read"}),
        SimpleNamespace(state="failed", result={"secret": "never read"}),
    ]
    events = [
        SimpleNamespace(kind="task.failed", created_at=(now - timedelta(hours=2)).isoformat(), payload={"token": "secret"}),
        SimpleNamespace(kind="task.progress", created_at=(now - timedelta(hours=1)).isoformat(), payload={"secret": "ignored"}),
        SimpleNamespace(kind="task.blocked", created_at=(now - timedelta(days=2)).isoformat(), payload={}),
    ]
    summary = build_operational_summary(tasks=tasks, events=events, period="daily", now=now)
    assert summary.tasks_observed == 2
    assert summary.task_states == {"running": 1, "failed": 1}
    assert summary.event_counts == {"task.failed": 1}
    assert "secret" not in summary.render()
    assert any("failure event" in item for item in summary.attention_items)


def test_weekly_summary_includes_seven_day_events_and_uses_utc():
    now = datetime(2026, 11, 23, 8, tzinfo=timezone(timedelta(hours=4)))
    event = SimpleNamespace(
        kind="task.interrupted",
        created_at=(now.astimezone(timezone.utc) - timedelta(days=6)).isoformat(),
        payload={"private": True},
    )
    summary = build_operational_summary(tasks=[], events=[event], period="weekly", now=now)
    assert summary.event_counts == {"task.interrupted": 1}
    assert summary.started_at.endswith("+00:00")


@pytest.mark.parametrize("period", ["hourly", "monthly", "", None])
def test_invalid_period_rejected(period):
    with pytest.raises(ValueError):
        build_operational_summary(tasks=[], events=[], period=period)


def test_naive_event_timestamp_is_ignored():
    event = SimpleNamespace(kind="task.failed", created_at="2026-11-23T07:00:00")
    summary = build_operational_summary(
        tasks=[], events=[event],
        now=datetime(2026, 11, 23, 8, tzinfo=timezone.utc),
    )
    assert summary.event_counts == {}
