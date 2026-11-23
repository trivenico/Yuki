import pytest

from core.memory_consolidation import MemoryConsolidator


def _entry(memory_id, text, *, version=1, confidence=0.5, use_count=0, status="active"):
    return {
        "id": memory_id,
        "text": text,
        "version": version,
        "confidence": confidence,
        "use_count": use_count,
        "status": status,
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def test_proposes_duplicate_pair_without_mutating_snapshot():
    entries = [
        _entry("older", "Yuki should retain verified router inventory details"),
        _entry("newer", "Yuki should retain verified router inventory details", version=3, confidence=0.8),
    ]
    before = [dict(item) for item in entries]

    proposals = MemoryConsolidator.propose(entries)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.suggested_keep_id == "newer"
    assert proposal.suggested_archive_id == "older"
    assert proposal.keep_version == 3
    assert proposal.archive_version == 1
    assert proposal.similarity == 1.0
    assert entries == before


def test_does_not_suggest_archived_or_unrelated_memories():
    entries = [
        _entry("active", "Yuki remembers verified router inventory"),
        _entry("archived", "Yuki remembers verified router inventory", status="archived"),
        _entry("other", "Calendar scheduling and meeting reminders"),
    ]

    assert MemoryConsolidator.propose(entries) == []


def test_sorts_best_matches_before_applying_limit():
    entries = [
        _entry("a", "alpha beta gamma delta epsilon"),
        _entry("b", "alpha beta gamma delta epsilon"),
        _entry("c", "alpha beta gamma delta zeta"),
    ]

    proposals = MemoryConsolidator.propose(entries, limit=1)

    assert len(proposals) == 1
    assert proposals[0].similarity == 1.0


@pytest.mark.parametrize(
    "entries,kwargs",
    [
        ([_entry("x", "text"), _entry("x", "text")], {}),
        ([_entry("x", "text", confidence=float("nan"))], {}),
        ([_entry("x", "text", version=True)], {}),
        ([_entry("x", "text", status="pending")], {}),
        ([_entry("x", "text")], {"similarity_threshold": 0.5}),
        ([_entry("x", "text")], {"limit": True}),
        ([{"id": "x", "text": "text", "version": 1}], {}),
    ],
)
def test_rejects_invalid_memory_snapshots_and_options(entries, kwargs):
    with pytest.raises(ValueError):
        MemoryConsolidator.propose(entries, **kwargs)


def test_bounds_snapshot_size():
    entries = [_entry(str(index), "memory text") for index in range(201)]
    with pytest.raises(ValueError):
        MemoryConsolidator.propose(entries)
