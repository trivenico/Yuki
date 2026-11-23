from types import SimpleNamespace

import pytest

from ui.awareness_views import render_operational_awareness


def test_awareness_view_renders_only_summary_and_attention_items():
    class Service:
        def snapshot(self, *, actor_id, current_goal):
            assert actor_id == "owner-1"
            assert current_goal == "Ship safely"
            return SimpleNamespace(
                snapshot=SimpleNamespace(
                    mode="background", current_goal=current_goal,
                    known_context=("2 tasks; 1 running",), open_questions=(),
                    recent_events=("1 task needs review",), uncertainty="medium",
                ),
                attention_items=("Task t-1 is failed and may need review.",),
            )

    rendered = render_operational_awareness(
        Service(), actor_id="owner-1", current_goal="Ship safely",
    )
    assert "Task t-1 is failed" in rendered
    assert "no actions taken" in rendered
    assert "untrusted context" in rendered


def test_awareness_view_rejects_missing_actor_and_bad_service():
    with pytest.raises(ValueError):
        render_operational_awareness(object(), actor_id="")
    with pytest.raises(TypeError):
        render_operational_awareness(object(), actor_id="owner-1")
