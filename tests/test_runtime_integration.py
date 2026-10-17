import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from companion.memory import MemoryStore
from companion.proactive import ReminderQueue
from core.agent import YukiCoreAgent
from core.config import load_config
from core.permissions import PermissionGate
from skills import TimeSkill
from skills.registry import SkillRegistry


class RuntimeIntegrationTests(unittest.TestCase):
    def test_config_loads_safe_defaults(self):
        config = load_config()
        self.assertEqual(config["name"], "Yuki")
        self.assertFalse(config["voice_enabled"])
        self.assertGreaterEqual(config["max_agent_steps"], 1)
        self.assertLessEqual(config["max_agent_steps"], 5)

    def test_workspace_environment_override(self):
        with patch.dict(os.environ, {"YUKI_WORKSPACE": "./test-workspace-override"}):
            config = load_config()
        self.assertTrue(config["workspace"].endswith("test-workspace-override"))

    def test_yaml_invalid_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("max_agent_steps: not-an-integer\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_skill_requires_explicit_approval(self):
        agent = YukiCoreAgent([TimeSkill()], PermissionGate())
        result = agent.run_skill("time")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "permission_denied")

    def test_unknown_skill_is_not_executed(self):
        agent = YukiCoreAgent([TimeSkill()], PermissionGate(lambda _: True))
        result = agent.run_skill("shell", "whoami")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "blocked")

    def test_registry_is_used_by_agent_and_rejects_duplicates(self):
        registry = SkillRegistry([TimeSkill()])
        agent = YukiCoreAgent(registry=registry, permission_gate=PermissionGate(lambda _: True))
        self.assertEqual(agent.registry.names(), ("time",))
        with self.assertRaises(ValueError):
            registry.register(TimeSkill())

    def test_memory_add_list_delete_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.json")
            item = store.add("Remember this")
            self.assertEqual(store.list()[0]["text"], "Remember this")
            self.assertTrue(store.delete(item["id"]))
            store.add("Another note")
            self.assertEqual(store.clear(), 1)
            self.assertEqual(store.list(), [])

    def test_memory_rejects_empty_or_oversized_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.json")
            with self.assertRaises(ValueError):
                store.add(" ")
            with self.assertRaises(ValueError):
                store.add("x" * (store.MAX_TEXT + 1))

    def test_reminders_require_timezone_and_deliver_once(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = ReminderQueue(Path(directory) / "reminders.json")
            with self.assertRaises(ValueError):
                queue.add("no timezone", datetime.now())
            item = queue.add("check task", datetime.now(timezone.utc) - timedelta(seconds=1))
            self.assertEqual(queue.due()[0]["id"], item["id"])
            self.assertEqual(queue.due(), [])

if __name__ == "__main__":
    unittest.main()
