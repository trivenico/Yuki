from automation.workflow_store import WorkflowRunStore


def test_list_recent_task_events_returns_newest_events_in_chronological_order(tmp_path):
    store = WorkflowRunStore(str(tmp_path / "workflow.sqlite3"))
    run = store.create(
        idempotency_key="recent-events",
        workflow_name="event-check",
        definition=[],
        owner_id="owner-a",
        initial_event={
            "kind": "task.created", "actor_id": "owner-a", "payload": {},
        },
    )
    with store._connect() as db:
        for sequence in range(2, 6):
            db.execute(
                "INSERT INTO task_events "
                "(task_id, sequence, kind, actor_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run.run_id, sequence, "task.progress", "owner-a", "{}", f"2026-11-23T00:00:0{sequence}+00:00"),
            )
    events = store.list_recent_task_events(run.run_id, limit=2)
    assert [event.sequence for event in events] == [4, 5]
    assert [event.kind for event in events] == ["task.progress", "task.progress"]
