from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.recurring_operational_signals import detect_recurring_signals


def test_detects_repeated_failure_category_and_compares_prior_window():
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    events = [
        SimpleNamespace(kind="task.failed", created_at=(now - timedelta(days=offset)).isoformat(), payload={"secret": "x"})
        for offset in (1, 2, 3)
    ] + [
        SimpleNamespace(kind="task.failed", created_at=(now - timedelta(days=8)).isoformat(), payload={})
    ]
    report = detect_recurring_signals(events=events, window_days=7, threshold=3, now=now)
    assert len(report.signals) == 1
    signal = report.signals[0]
    assert (signal.kind, signal.current_count, signal.previous_count) == ("task.failed", 3, 1)
    assert signal.trend == "increasing"
    assert "secret" not in report.render()
    assert "not proven root causes" in report.render()


def test_ignores_unapproved_event_kinds_and_naive_timestamps():
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    events = [
        SimpleNamespace(kind="task.progress", created_at=now.isoformat()),
        SimpleNamespace(kind="task.failed", created_at="2026-11-22T00:00:00"),
    ]
    report = detect_recurring_signals(events=events, now=now)
    assert report.signals == ()


def test_equal_counts_are_reported_as_unchanged():
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    events = [
        SimpleNamespace(kind="task.blocked", created_at=(now - timedelta(days=d)).isoformat())
        for d in (1, 2, 8, 9)
    ]
    report = detect_recurring_signals(events=events, now=now, threshold=2)
    assert report.signals[0].trend == "unchanged"


@pytest.mark.parametrize("window,threshold", [(0, 3), (31, 3), (7, 1), (7, 1001)])
def test_rejects_out_of_range_configuration(window, threshold):
    with pytest.raises(ValueError):
        detect_recurring_signals(
            events=[], window_days=window, threshold=threshold,
            now=datetime(2026, 11, 23, tzinfo=timezone.utc),
        )


def test_bounds_consumption_of_unbounded_iterable():
    def endless():
        while True:
            yield SimpleNamespace(kind="task.failed", created_at="2026-11-22T00:00:00+00:00")
    with pytest.raises(ValueError, match="10000-record"):
        detect_recurring_signals(
            events=endless(), now=datetime(2026, 11, 23, tzinfo=timezone.utc),
        )
