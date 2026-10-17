import tempfile
import unittest
from pathlib import Path

from companion import DEFAULT_PERSONA, EmotionState
from core.agent import YukiCoreAgent
from core.permissions import ApprovalRequest, PermissionGate
from skills import FileSkill, SystemSkill, TimeSkill


class CompanionSkillAgentTests(unittest.TestCase):
    def test_persona_uses_yuki_wake_word(self):
        self.assertEqual(DEFAULT_PERSONA.wake_word, "Yuki")
        self.assertIn("Hai, Master", DEFAULT_PERSONA.greeting)

    def test_emotion_rejects_unknown_tone(self):
        state = EmotionState()
        with self.assertRaises(ValueError):
            state.set_tone("mind_reading")

    def test_gate_fails_closed_without_approver(self):
        self.assertFalse(PermissionGate().request("skill:time", "show time"))

    def test_gate_requires_true_boolean(self):
        gate = PermissionGate(lambda request: 1)
        self.assertFalse(gate.request("skill:time", "show time"))

    def test_approved_time_skill_runs(self):
        seen = []
        gate = PermissionGate(lambda request: seen.append(request) or True)
        agent = YukiCoreAgent([TimeSkill()], gate)
        result = agent.run_skill("time")
        self.assertTrue(result.ok)
        self.assertEqual(result.stage, "report")
        self.assertIsInstance(seen[0], ApprovalRequest)

    def test_unapproved_skill_does_not_run(self):
        agent = YukiCoreAgent([TimeSkill()])
        result = agent.run_skill("time")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "permission_denied")

    def test_unknown_skill_is_blocked(self):
        agent = YukiCoreAgent([TimeSkill()], PermissionGate(lambda _: True))
        self.assertEqual(agent.run_skill("shell").stage, "blocked")

    def test_file_skill_lists_only_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "folder").mkdir()
            (root / "note.txt").write_text("private contents", encoding="utf-8")
            skill = FileSkill(root)
            result = skill.execute(".")
            self.assertTrue(result.ok)
            self.assertEqual({entry["name"] for entry in result.data}, {"folder", "note.txt"})
            outside = skill.execute("../")
            self.assertFalse(outside.ok)
            self.assertNotIn("private contents", str(result.data))

    def test_read_only_system_skill(self):
        result = SystemSkill().execute()
        self.assertTrue(result.ok)
        self.assertIn("python", result.data)


if __name__ == "__main__":
    unittest.main()
