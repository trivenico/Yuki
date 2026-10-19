import tempfile
import unittest
from pathlib import Path

from skills.proposals import SkillProposalStore
from skills.registry import SkillRegistry


DOC = "---\nname: safe-notes\ndescription: Summarize supplied notes\nversion: 1.0.0\n---\n\nSummarize only the text supplied by the user. Never call tools.\n"


class DynamicSkillReviewTests(unittest.TestCase):
    def test_rejection_archives_proposal_and_prevents_name_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillProposalStore(directory)
            store.propose(DOC)
            with self.assertRaises(PermissionError):
                store.reject("safe-notes", human_rejected=False)
            rejected = store.reject("safe-notes", human_rejected=True)
            self.assertTrue(rejected.exists())
            self.assertEqual(store.list_proposals(), [])
            with self.assertRaises(ValueError):
                store.propose(DOC)

    def test_approved_documents_load_as_inert_registry_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillProposalStore(directory)
            store.propose(DOC)
            approved = store.approve("safe-notes", human_approved=True)
            registry = SkillRegistry()
            loaded = registry.load_approved_documents(approved.parent)
            self.assertEqual(loaded, ["safe-notes"])
            skill = registry.get("safe-notes")
            result = skill.execute("hello")
            self.assertTrue(result.ok)
            self.assertIn("instructions", result.data)
            self.assertIn("Never call tools", result.data["instructions"])
            self.assertIn("safe-notes", registry.names())

    def test_approved_loader_skips_symlinks_and_invalid_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.SKILL.md").write_text("not frontmatter", encoding="utf-8")
            registry = SkillRegistry()
            self.assertEqual(registry.load_approved_documents(root), [])
            self.assertEqual(len(registry), 0)


if __name__ == "__main__":
    unittest.main()
