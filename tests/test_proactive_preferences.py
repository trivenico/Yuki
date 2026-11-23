import pytest

from core.proactive_preferences import ProactiveNotificationPreferences


def test_preferences_default_to_disabled_and_persist_per_owner(tmp_path):
    store = ProactiveNotificationPreferences(tmp_path / "preferences.sqlite3")
    assert store.get(owner_id="owner-a")["notifications_enabled"] is False
    assert store.set(owner_id="owner-a", notifications_enabled=True)["notifications_enabled"] is True

    reopened = ProactiveNotificationPreferences(tmp_path / "preferences.sqlite3")
    assert reopened.get(owner_id="owner-a")["notifications_enabled"] is True
    assert reopened.get(owner_id="owner-b")["notifications_enabled"] is False


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_preferences_reject_non_boolean_values(tmp_path, value):
    store = ProactiveNotificationPreferences(tmp_path / "preferences.sqlite3")
    with pytest.raises(ValueError):
        store.set(owner_id="owner-a", notifications_enabled=value)


def test_preferences_reject_invalid_owner(tmp_path):
    store = ProactiveNotificationPreferences(tmp_path / "preferences.sqlite3")
    with pytest.raises(ValueError):
        store.get(owner_id="\x00")
