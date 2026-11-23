import pytest

from core.learning_consolidation import MemoryConsolidator


def test_consolidator_flags_lexical_duplicates_without_mutating_snapshot():
    entries = [
        {"id": "m1", "version": 3, "text": "Use bounded retries for transient worker failures."},
        {"id": "m2", "version": 1, "text": "Use bounded retries for transient worker failures."},
        {"id": "m3", "version": 2, "text": "Prepare a weekly project status report."},
        {"id": "old", "version": 4, "status": "archived", "text": "Use bounded retries for transient worker failures."},
    ]
    original = [dict(item) for item in entries]

    report = MemoryConsolidator.analyze(entries)

    assert report.memories_examined == 3
    assert report.duplicate_review_count == 1
    assert report.related_review_count == 0
    finding = report.findings[0]
    assert finding.memory_ids == ("m1", "m2")
    assert finding.memory_versions == (3, 1)
    assert finding.signal == "possible_duplicate_review"
    assert entries == original


def test_related_overlap_is_not_reported_as_a_duplicate():
    report = MemoryConsolidator.analyze([
        {"id": "a", "text": "Yuki should verify worker lease ownership before task completion."},
        {"id": "b", "text": "Yuki should verify worker lease ownership before task retry."},
    ])

    assert report.related_review_count == 1
    assert report.duplicate_review_count == 0
    assert report.findings[0].signal == "related_memory_review"


def test_consolidator_does_not_infer_semantic_conflicts_or_edit_memory():
    entries = [
        {"id": "a", "version": 1, "text": "Always use local storage for private notes."},
        {"id": "b", "version": 1, "text": "Never use local storage for private notes."},
    ]
    report = MemoryConsolidator.analyze(entries)

    assert report.findings
    assert all("conflict" not in item.signal for item in report.findings)
    assert entries[0]["text"].startswith("Always")
    assert entries[1]["text"].startswith("Never")


@pytest.mark.parametrize("entries", [
    [],
    [{"id": "same", "text": "First valid memory."}, {"id": "same", "text": "Second valid memory."}],
    [{"id": "a", "version": True, "text": "Invalid version."}],
    [{"id": "a", "text": ""}],
    [{"id": "a", "status": "pending", "text": "Invalid status."}],
])
def test_consolidator_rejects_invalid_snapshot(entries):
    if entries == []:
        # An empty snapshot is valid and produces an empty report.
        assert MemoryConsolidator.analyze(entries).memories_examined == 0
    else:
        with pytest.raises((TypeError, ValueError)):
            MemoryConsolidator.analyze(entries)


def test_consolidator_bounds_snapshot_size():
    entries = [
        {"id": str(index), "text": "A valid memory entry."}
        for index in range(201)
    ]
    with pytest.raises(ValueError):
        MemoryConsolidator.analyze(entries)
