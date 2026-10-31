import tempfile
import unittest
from pathlib import Path

from core.skill_environment_manager import (
    SkillEnvironmentError, plan_skill_environment, render_hashed_requirements,
    validate_hashed_lockfile,
)


class SkillEnvironmentManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_root = self.root / "environments"
        self.env_root.mkdir()
        self.python = self.root / "python"
        self.python.write_text("placeholder", encoding="utf-8")
        self.wheelhouse = self.root / "wheelhouse"
        self.wheelhouse.mkdir()
        self.entries = [{
            "name": "PyYAML", "version": "6.0.2",
            "sha256": "a" * 64,
        }]

    def test_lockfile_is_normalized_and_deterministic(self):
        entries = [
            {"name": "Requests", "version": "2.31.0", "sha256": "b" * 64},
            {"name": "Py_YAML", "version": "6.0.2", "sha256": "a" * 64},
        ]
        rendered = render_hashed_requirements(entries)
        self.assertEqual(rendered.splitlines()[0], f"py-yaml==6.0.2 --hash=sha256:{'a' * 64}")
        self.assertEqual(rendered, render_hashed_requirements(list(reversed(entries))))

    def test_rejects_unhashed_unpinned_duplicate_and_oversized_entries(self):
        bad = [
            [{"name": "requests", "version": "2.0", "sha256": "bad"}],
            [{"name": "requests", "version": ">=2.0", "sha256": "a" * 64}],
            [{"name": "requests", "version": "2.0", "sha256": "a" * 64},
             {"name": "Requests", "version": "2.1", "sha256": "b" * 64}],
            [{"name": f"pkg{index}", "version": "1.0", "sha256": "a" * 64} for index in range(129)],
        ]
        for entries in bad:
            with self.subTest(entries=len(entries)), self.assertRaises(SkillEnvironmentError):
                validate_hashed_lockfile(entries)

    def test_plan_is_offline_hash_locked_and_has_no_side_effects(self):
        plan = plan_skill_environment(
            skill_id="writer", skill_version="1.2.0", environment_root=self.env_root,
            python_executable=self.python, wheelhouse=self.wheelhouse, lock_entries=self.entries,
        )
        self.assertIn("--no-index", plan.install_argv)
        self.assertIn("--require-hashes", plan.install_argv)
        self.assertIn("--only-binary=:all:", plan.install_argv)
        self.assertEqual(plan.package_count, 1)
        self.assertEqual(plan.side_effects, ())
        self.assertFalse(plan.as_dict()["commands_executed"])
        self.assertFalse(Path(plan.environment_path).exists())
        self.assertIn("--hash=sha256:", plan.lockfile_content)

    def test_rejects_existing_target_and_empty_lockfile(self):
        target = self.env_root / "writer" / "1.2.0"
        target.mkdir(parents=True)
        with self.assertRaisesRegex(SkillEnvironmentError, "already exists"):
            plan_skill_environment(skill_id="writer", skill_version="1.2.0",
                environment_root=self.env_root, python_executable=self.python,
                wheelhouse=self.wheelhouse, lock_entries=self.entries)
        target.rmdir()
        with self.assertRaisesRegex(SkillEnvironmentError, "empty lockfile"):
            plan_skill_environment(skill_id="writer", skill_version="1.2.0",
                environment_root=self.env_root, python_executable=self.python,
                wheelhouse=self.wheelhouse, lock_entries=[])

    def test_rejects_symlinked_paths_and_relative_paths(self):
        link = self.root / "linked-wheelhouse"
        try:
            link.symlink_to(self.wheelhouse, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(SkillEnvironmentError):
            plan_skill_environment(skill_id="writer", skill_version="1.2.0",
                environment_root=self.env_root, python_executable=self.python,
                wheelhouse=link, lock_entries=self.entries)
        with self.assertRaises(SkillEnvironmentError):
            plan_skill_environment(skill_id="writer", skill_version="1.2.0",
                environment_root="relative", python_executable=self.python,
                wheelhouse=self.wheelhouse, lock_entries=self.entries)


if __name__ == "__main__":
    unittest.main()