import unittest
from skills.course_coach import CourseCoachSkill
from companion.mood_engine import MoodEngine

class CourseCoachTests(unittest.TestCase):
    def setUp(self):
        self.skill = CourseCoachSkill()

    def test_rejects_empty_and_unknown_modes(self):
        self.assertFalse(self.skill.run("").ok)
        self.assertFalse(self.skill.run("do_exam").ok)
        self.assertFalse(self.skill.run("quiz").ok)

    def test_supported_learning_modes_are_bounded_and_safe(self):
        for mode in ("explain", "lab", "quiz", "study_plan", "interview"):
            with self.subTest(mode=mode):
                result = self.skill.run(mode + " cybersecurity fundamentals")
                self.assertTrue(result.ok)
                self.assertIn("no commands", result.message)
        self.assertFalse(self.skill.run("quiz " + "x" * 451).ok)

    def test_lab_mode_emphasizes_authorized_scope(self):
        result = self.skill.run("lab investigate suspicious login in course sandbox")
        self.assertIn("authorised", str(result.data).lower())
        self.assertIn("No commands were run", result.data["safety"])

    def test_mood_output_has_stable_shape_with_and_without_report(self):
        engine = MoodEngine()
        neutral = engine.adapt("hello")
        self.assertEqual(neutral["style"], "natural and conversational")
        self.assertIsNone(neutral["mood"])
        sad = engine.set_mood("sad")
        adapted = engine.adapt("hello")
        self.assertEqual(adapted["style"], "gentle and supportive")
        self.assertEqual(adapted["mood"], "sad")
        self.assertEqual(sad.source, "user_report")
        engine.clear()
        self.assertIsNone(engine.adapt("hello")["mood"])

if __name__ == "__main__":
    unittest.main()
