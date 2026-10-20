import json
import tempfile
import unittest
from pathlib import Path

from skills.registry import SkillRegistry
from skills.prediction_engine import PredictionEngineSkill
from skills.multi_model_critique import MultiModelCritiqueSkill
from skills.ai_mentor import AIMentorSkill
from skills.ai_humanizer import AIHumanizerSkill
from skills.youtube_shorts import YouTubeShortsSkill
from skills.seo_geo_optimizer import SEOGEOOptimizerSkill
from skills.conversation_analyzer import ConversationAnalyzerSkill
from skills.vibe_coder import VibeCoderSkill
from skills.long_video_generator import LongVideoGeneratorSkill


class NextPhaseSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_registry_has_all_nine_skills(self):
        names = set(SkillRegistry.defaults(self.root).names())
        self.assertTrue(
            {
                "prediction_engine",
                "multi_model_critique",
                "ai_mentor",
                "ai_humanizer",
                "youtube_shorts",
                "seo_geo_optimizer",
                "conversation_analyzer",
                "vibe_coder",
                "long_video_generator",
            }.issubset(names)
        )

    def test_prediction_requires_multiple_supplied_forecasts(self):
        self.assertFalse(PredictionEngineSkill().execute('{"question":"q","forecasts":[]}').ok)
        result = PredictionEngineSkill().execute(
            '{"question":"q","forecasts":[{"agent":"a","probability":0.2},{"agent":"b","probability":0.8}]}'
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["mean_probability"], 0.5)

    def test_critique_fails_closed_without_two_models(self):
        self.assertFalse(MultiModelCritiqueSkill().execute('{"task":"review"}').ok)
        skill = MultiModelCritiqueSkill(lambda task, ctx: "draft", lambda task, draft: "critique")
        self.assertTrue(skill.execute('{"task":"review"}').ok)

    def test_mentor_persists_goal_and_progress(self):
        skill = AIMentorSkill(self.root)
        goal = skill.execute('{"action":"goal","topic":"Python","target":"Build CLI"}')
        self.assertTrue(goal.ok)
        self.assertTrue(
            skill.execute(
                json.dumps(
                    {"action": "progress", "id": goal.data["id"], "note": "finished parsing"}
                )
            ).ok
        )

    def test_humanizer_does_not_claim_detector_evasion(self):
        result = AIHumanizerSkill().execute('{"text":"A sample paragraph."}')
        self.assertTrue(result.ok)
        self.assertIn("does not evade", result.message)

    def test_video_planners_do_not_claim_rendering(self):
        self.assertIn("no TTS", YouTubeShortsSkill().execute('{"topic":"testing"}').message)
        self.assertIn(
            "no script model", LongVideoGeneratorSkill().execute('{"topic":"testing"}').message
        )

    def test_seo_checklist_is_offline_and_bounded(self):
        result = SEOGEOOptimizerSkill().execute(
            '{"title":"Title","description":"Summary","content":"# Header\\nThis is answer content with references and source: example. "}'
        )
        self.assertTrue(result.ok)
        self.assertIn("limitations", result.data)

    def test_conversation_export_stays_in_workspace(self):
        path = self.root / "export.json"
        path.write_text(json.dumps({"messages": [{"role": "user", "content": "hello"}]}))
        self.assertTrue(ConversationAnalyzerSkill(self.root).execute("export.json").ok)
        self.assertFalse(ConversationAnalyzerSkill(self.root).execute("../outside.json").ok)

    def test_vibe_coder_only_plans(self):
        result = VibeCoderSkill().execute('{"brief":"Build a notes app"}')
        self.assertTrue(result.ok)
        self.assertIn("no files were created", result.message)


if __name__ == "__main__":
    unittest.main()
