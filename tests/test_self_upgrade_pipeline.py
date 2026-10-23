import hashlib
import unittest

from automation.approved_workspace_changes import FileChange
from automation.authorized_read_tools import ToolInputError
from automation.self_upgrade_pipeline import SelfUpgradeProposalBuilder


class SelfUpgradeProposalBuilderTests(unittest.TestCase):
    def setUp(self):
        self.source = "VALUE = 1\n"
        self.digest = hashlib.sha256(self.source.encode()).hexdigest()
        self.builder = SelfUpgradeProposalBuilder(["src/example.py", "tests/test_example.py"])

    def test_builds_stable_digest_without_applying_changes(self):
        change = FileChange("src/example.py", self.digest, "VALUE = 2\n")
        proposal = self.builder.build([change])
        self.assertTrue(proposal.proposal_id.startswith("upgrade_"))
        self.assertEqual(proposal.changed_paths, ("src/example.py",))
        self.assertTrue(proposal.review_required)
        self.assertTrue(proposal.tests_required)
        self.assertFalse(proposal.promotion_allowed)

    def test_repeated_same_proposal_has_same_digest(self):
        change = FileChange("src/example.py", self.digest, "VALUE = 2\n")
        first = self.builder.build([change])
        second = self.builder.build([change])
        self.assertEqual(first.content_digest, second.content_digest)

    def test_unlisted_path_rejected(self):
        change = FileChange("src/other.py", self.digest, "x=2\n")
        with self.assertRaises(ToolInputError):
            self.builder.build([change])

    def test_sensitive_path_rejected_even_if_allowlisted(self):
        builder = SelfUpgradeProposalBuilder([".env"])
        change = FileChange(".env", self.digest, "TOKEN=example\n")
        with self.assertRaises(ToolInputError):
            builder.build([change])

    def test_duplicate_paths_rejected(self):
        change = FileChange("src/example.py", self.digest, "VALUE = 2\n")
        with self.assertRaises(ToolInputError):
            self.builder.build([change, change])

    def test_bad_digest_and_nul_content_rejected(self):
        with self.assertRaises(ToolInputError):
            self.builder.build([FileChange("src/example.py", "bad", "x=2\n")])
        with self.assertRaises(ToolInputError):
            self.builder.build([FileChange("src/example.py", self.digest, "x=2\x00")])

    def test_change_count_is_bounded(self):
        changes = [
            FileChange("src/example.py", self.digest, "x=2\n"),
            FileChange("tests/test_example.py", self.digest, "x=3\n"),
        ] * 5
        with self.assertRaises(ToolInputError):
            self.builder.build(changes)


if __name__ == "__main__":
    unittest.main()
