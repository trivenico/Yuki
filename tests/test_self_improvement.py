import unittest

from core.self_improvement import (
    ProposedFile,
    ProposalRejected,
    validate_change_proposal,
)


class SelfImprovementProposalTests(unittest.TestCase):
    def test_regular_change_is_only_a_proposal(self):
        proposal = validate_change_proposal(
            title="Improve diagnostics",
            rationale="Add a bounded diagnostic message.",
            files=[ProposedFile("docs/diagnostics.md", "# Diagnostics\n")],
        )
        self.assertEqual(proposal.status, "proposal_only")
        self.assertFalse(proposal.requires_human_approval)
        self.assertEqual(len(proposal.sha256), 64)

    def test_protected_runtime_change_requires_human_approval(self):
        proposal = validate_change_proposal(
            title="Change permissions",
            rationale="Review a permission change.",
            files=[ProposedFile("core/permissions.py", "# proposed only\n")],
        )
        self.assertTrue(proposal.requires_human_approval)
        self.assertEqual(proposal.status, "awaiting_human_approval")

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        for path in (
            "../outside.py", "/tmp/outside.py", "C:/outside.py",
            "core/../secrets.py", "docs//notes.md", "docs/./notes.md",
            "docs/name./notes.md", "docs/name /notes.md", "NUL.txt",
            "docs/report.txt:stream",
        ):
            with self.subTest(path=path), self.assertRaises(ProposalRejected):
                validate_change_proposal(
                    title="Invalid",
                    rationale="Invalid path test.",
                    files=[ProposedFile(path, "content")],
                )

    def test_duplicate_case_insensitive_paths_are_rejected(self):
        with self.assertRaises(ProposalRejected):
            validate_change_proposal(
                title="Duplicates",
                rationale="Avoid path collisions.",
                files=[ProposedFile("Core/Agent.py", "a"), ProposedFile("core/agent.py", "b")],
            )

    def test_secrets_binary_and_size_are_rejected(self):
        for path in ("keys/private.pem", ".git/config"):
            with self.subTest(path=path), self.assertRaises(ProposalRejected):
                validate_change_proposal(
                    title="Forbidden",
                    rationale="Forbidden path test.",
                    files=[ProposedFile(path, "secret")],
                )
        with self.assertRaises(ProposalRejected):
            validate_change_proposal(
                title="Binary",
                rationale="NUL bytes should fail.",
                files=[ProposedFile("data.txt", "hello\x00world")],
            )

    def test_bounds_number_of_files_and_content_size(self):
        with self.assertRaises(ProposalRejected):
            validate_change_proposal(
                title="Too many",
                rationale="Bound the proposal.",
                files=[ProposedFile(f"docs/{i}.md", "x") for i in range(21)],
            )
        with self.assertRaises(ProposalRejected):
            validate_change_proposal(
                title="Too large",
                rationale="Bound each file.",
                files=[ProposedFile("docs/large.md", "x" * 256_001)],
            )

    def test_proposal_hash_is_stable_for_file_order(self):
        a = validate_change_proposal(
            title="Stable",
            rationale="Same files in different order.",
            files=[ProposedFile("docs/a.md", "a"), ProposedFile("docs/b.md", "b")],
        )
        b = validate_change_proposal(
            title="Stable",
            rationale="Same files in different order.",
            files=[ProposedFile("docs/b.md", "b"), ProposedFile("docs/a.md", "a")],
        )
        self.assertEqual(a.sha256, b.sha256)


if __name__ == "__main__":
    unittest.main()
