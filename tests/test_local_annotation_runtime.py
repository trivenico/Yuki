"""Security and lifecycle tests for local annotation runtime composition."""
import os
import tempfile
import unittest
from pathlib import Path

from core.local_annotation_runtime import build_local_annotation_runtime


class LocalAnnotationRuntimeTests(unittest.TestCase):
    def test_runtime_uses_stable_local_owner_and_private_separate_database(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            first = build_local_annotation_runtime({"workspace": str(workspace)})
            second = build_local_annotation_runtime({"workspace": str(workspace)})
            self.assertEqual(first.owner_id, second.owner_id)
            self.assertTrue(first.owner_id.startswith("local-"))
            database = workspace / "annotation" / "workbench.sqlite3"
            self.assertTrue(database.is_file())
            if os.name == "posix":
                self.assertEqual(
                    (workspace / "annotation").stat().st_mode & 0o777, 0o700,
                )
                self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            project = first.workbench.create_project(
                first.owner_id, name="Test", task_type="image_classification",
                instructions="Choose the label.", rubric={"labels": ["bird"]},
            )
            self.assertEqual(
                second.workbench.list_projects(second.owner_id)[0].project_id,
                project.project_id,
            )

    def test_owner_factory_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "owner_identity"):
                build_local_annotation_runtime(
                    {"workspace": directory}, owner_id_factory=lambda: "",
                )

    def test_symlink_annotation_directory_is_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            target = root / "target"
            target.mkdir()
            try:
                (workspace / "annotation").symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted")
            with self.assertRaisesRegex(ValueError, "must_not_be_symlink"):
                build_local_annotation_runtime({"workspace": str(workspace)})

    def test_non_absolute_workspace_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "must_be_absolute"):
            build_local_annotation_runtime({"workspace": "relative/workspace"})


if __name__ == "__main__":
    unittest.main()
