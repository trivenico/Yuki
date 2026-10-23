import unittest

from core.memory_conflicts import (
    MemoryConflictError,
    MemoryVersion,
    reconcile_memory_versions,
    version_fingerprint,
)


class MemoryConflictTests(unittest.TestCase):
    def setUp(self):
        self.base = MemoryVersion(
            memory_id="note-1", version_id="v1", origin_id="laptop-1",
            sequence=1, text="original", category="note",
        )

    def test_identical_version_is_idempotent(self):
        result = reconcile_memory_versions(self.base, self.base)
        self.assertEqual(result.status, "identical")
        self.assertEqual(result.versions, (self.base,))

    def test_child_version_is_recognized_without_dropping_parent(self):
        child = MemoryVersion(
            memory_id="note-1", version_id="v2", origin_id="laptop-1",
            sequence=2, text="updated", parent_version_id="v1",
        )
        result = reconcile_memory_versions(self.base, child)
        self.assertEqual(result.status, "incoming_is_child")
        self.assertEqual(result.versions, (self.base, child))

    def test_concurrent_edits_are_preserved_and_conflict_id_is_stable(self):
        left = MemoryVersion(
            memory_id="note-1", version_id="v2-local", origin_id="laptop-1",
            sequence=2, text="local edit", parent_version_id="v1",
        )
        right = MemoryVersion(
            memory_id="note-1", version_id="v2-cloud", origin_id="cloud-1",
            sequence=2, text="cloud edit", parent_version_id="v1",
        )
        first = reconcile_memory_versions(left, right)
        second = reconcile_memory_versions(right, left)
        self.assertEqual(first.status, "conflict")
        self.assertEqual(len(first.versions), 2)
        self.assertEqual(first.conflict_id, second.conflict_id)
        self.assertEqual(first.versions, second.versions)

    def test_different_memories_and_malformed_versions_fail_closed(self):
        other = MemoryVersion(
            memory_id="note-2", version_id="v2", origin_id="cloud-1",
            sequence=2, text="different",
        )
        with self.assertRaises(MemoryConflictError):
            reconcile_memory_versions(self.base, other)
        invalid = MemoryVersion(
            memory_id="note-1", version_id="v2", origin_id="cloud-1",
            sequence=True, text="bad",
        )
        with self.assertRaises(MemoryConflictError):
            version_fingerprint(invalid)

    def test_untrusted_category_types_fail_with_domain_error(self):
        invalid = MemoryVersion(
            memory_id="note-1", version_id="v2", origin_id="cloud-1",
            sequence=2, text="bad category", category=["note"],
        )
        with self.assertRaises(MemoryConflictError):
            version_fingerprint(invalid)

    def test_versions_without_ancestry_are_not_assumed_to_be_ordered(self):
        other = MemoryVersion(
            memory_id="note-1", version_id="v2", origin_id="cloud-1",
            sequence=9, text="different without parent",
        )
        result = reconcile_memory_versions(self.base, other)
        self.assertEqual(result.status, "conflict")
        self.assertEqual(len(result.versions), 2)


if __name__ == "__main__":
    unittest.main()
