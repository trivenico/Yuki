from pathlib import Path
from types import SimpleNamespace

from core.durable_knowledge_runtime import DurableKnowledgeRuntime


class FakeIndexer:
    def __init__(self):
        self.calls = []

    def index_file(self, path, *, workspace):
        self.calls.append((path, Path(workspace)))
        return {"source": path, "chunks_indexed": 1}


class FakeCoordinator:
    def __init__(self):
        self.action = None

    def record(self, planner, result, *, actor, session_id):
        self.action = result["action"]
        return SimpleNamespace(pending_id="pending-1")

    def approve(self, executor, pending_id, *, actor, session_id, confirm):
        if confirm(self.action) is not True:
            return SimpleNamespace(status="approval_denied", result=None)
        result = executor.execute_approved_action(self.action)
        return SimpleNamespace(status="completed", result=result)


def build_runtime(tmp_path, confirm=lambda action: True):
    source = tmp_path / "notes.md"
    source.write_text("safe note", encoding="utf-8")
    indexer = FakeIndexer()
    runtime = DurableKnowledgeRuntime(
        indexer=indexer, workspace=tmp_path, coordinator=FakeCoordinator(),
        actor="test-owner", confirm=confirm,
    )
    return runtime, indexer, source


def test_index_uses_durable_approval_and_workspace_relative_path(tmp_path):
    runtime, indexer, source = build_runtime(tmp_path)
    result = runtime.index("notes.md")
    assert result["ok"] is True
    assert indexer.calls == [("notes.md", tmp_path.resolve())]


def test_denied_approval_does_not_index(tmp_path):
    runtime, indexer, _ = build_runtime(tmp_path, confirm=lambda action: False)
    result = runtime.index("notes.md")
    assert result["ok"] is False
    assert result["status"] == "approval_denied"
    assert indexer.calls == []


def test_rejects_absolute_path(tmp_path):
    runtime, indexer, source = build_runtime(tmp_path)
    result = runtime.index(str(source))
    assert result["ok"] is False
    assert result["status"] == "invalid_input"
    assert indexer.calls == []


def test_rejects_path_traversal(tmp_path):
    runtime, indexer, _ = build_runtime(tmp_path)
    result = runtime.index("../outside.md")
    assert result["ok"] is False
    assert result["status"] == "invalid_input"
    assert indexer.calls == []


def test_rejects_symlink_escaping_workspace(tmp_path):
    runtime, indexer, _ = build_runtime(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside.md")
    outside.write_text("private", encoding="utf-8")
    link = tmp_path / "escape.md"
    link.symlink_to(outside)
    result = runtime.index("escape.md")
    assert result["ok"] is False
    assert result["status"] == "invalid_input"
    assert indexer.calls == []
