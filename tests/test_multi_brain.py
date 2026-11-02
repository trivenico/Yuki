import threading
import time
import unittest

from core.multi_brain import MultiBrainCoordinator


class FakeProvider:
    def __init__(self, name, response=None, available=True, delay=0):
        self.name=name; self.response=response or name; self.available=available; self.delay=delay; self.prompts=[]
    def is_available(self): return self.available
    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.delay: time.sleep(self.delay)
        return self.response


class MultiBrainTests(unittest.TestCase):
    def test_parallel_specialists_are_synthesized(self):
        a,b,s=FakeProvider("a"),FakeProvider("b"),FakeProvider("s","final answer")
        coordinator=MultiBrainCoordinator({"research":a,"coding":b},s)
        self.assertEqual(coordinator.generate("solve this"),"final answer")
        self.assertEqual(coordinator.last_report["successful"],["coding","research"])
        self.assertIn("[coding] b",s.prompts[0])
        self.assertIn("[research] a",s.prompts[0])
        self.assertIn("untrusted evidence",s.prompts[0])

    def test_unavailable_specialist_is_reported(self):
        good,bad,s=FakeProvider("good"),FakeProvider("bad",available=False),FakeProvider("s","ok")
        c=MultiBrainCoordinator({"good":good,"bad":bad},s,min_success=1)
        self.assertEqual(c.generate("task"),"ok")
        self.assertEqual(c.last_report["failed"][0]["error"],"RuntimeError")

    def test_minimum_success_is_enforced(self):
        a,b,s=FakeProvider("a",available=False),FakeProvider("b",available=False),FakeProvider("s")
        c=MultiBrainCoordinator({"a":a,"b":b},s,min_success=2)
        with self.assertRaisesRegex(RuntimeError,"Not enough specialist"):
            c.generate("task")
        self.assertFalse(s.prompts)

    def test_invalid_provider_output_is_ignored(self):
        a,b,s=FakeProvider(""),FakeProvider("valid"),FakeProvider("s","ok")
        c=MultiBrainCoordinator({"empty":a,"valid":b},s)
        self.assertEqual(c.generate("task"),"ok")
        self.assertEqual(c.last_report["failed"][0]["error"],"RuntimeError")

    def test_synthesis_unavailable_fails_closed(self):
        c=MultiBrainCoordinator({"a":FakeProvider("a")},FakeProvider("s",available=False))
        with self.assertRaisesRegex(RuntimeError,"synthesis provider"):
            c.generate("task")

    def test_bounds_and_prompt_validation(self):
        with self.assertRaises(ValueError):
            MultiBrainCoordinator({"a":FakeProvider("a")},FakeProvider("s"),max_workers=0)
        c=MultiBrainCoordinator({"a":FakeProvider("a")},FakeProvider("s"))
        with self.assertRaises(ValueError): c.generate(" ")
        with self.assertRaises(ValueError): c.generate("x"*20001)

    def test_status_does_not_enable_tools(self):
        c=MultiBrainCoordinator({"a":FakeProvider("a")},FakeProvider("s"))
        self.assertFalse(c.status()["tool_execution"])


if __name__=="__main__": unittest.main()
