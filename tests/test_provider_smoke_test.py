import unittest
from tools.provider_smoke_test import run_smoke_test

class FakeProvider:
    def __init__(self, response="YUKI_SMOKE_TEST_OK", available=True): self.response=response; self.available=available; self.calls=0
    def is_available(self): return self.available
    def generate(self, prompt): self.calls+=1; return self.response

class ProviderSmokeTestTests(unittest.TestCase):
    def test_real_provider_round_trip_passes_without_logging_response(self):
        provider=FakeProvider()
        ticks=iter([10.0,10.25])
        report=run_smoke_test(config_loader=lambda:{"provider":"ollama"},provider_factory=lambda config:provider,clock=lambda:next(ticks))
        self.assertEqual(report.status,"pass")
        self.assertEqual(report.elapsed_seconds,0.25)
        self.assertEqual(report.response_characters,len("YUKI_SMOKE_TEST_OK"))
        self.assertNotIn("YUKI_SMOKE_TEST_OK",str(report.to_dict()))
        self.assertEqual(provider.calls,1)
    def test_mock_is_not_misreported_as_real_ai(self):
        provider=FakeProvider()
        report=run_smoke_test(config_loader=lambda:{"provider":"mock"},provider_factory=lambda config:provider)
        self.assertEqual(report.status,"fail")
        self.assertEqual(provider.calls,0)
    def test_unavailable_provider_sends_no_prompt(self):
        provider=FakeProvider(available=False)
        report=run_smoke_test(config_loader=lambda:{"provider":"ollama"},provider_factory=lambda config:provider)
        self.assertEqual(report.status,"fail")
        self.assertEqual(provider.calls,0)
    def test_provider_exception_is_redacted(self):
        class Bad(FakeProvider):
            def generate(self,prompt): raise RuntimeError("token=secret-value")
        ticks=iter([2.0,2.1])
        report=run_smoke_test(config_loader=lambda:{"provider":"ollama"},provider_factory=lambda config:Bad(),clock=lambda:next(ticks))
        self.assertEqual(report.status,"fail")
        self.assertNotIn("secret-value",str(report.to_dict()))
    def test_invalid_response_fails(self):
        report=run_smoke_test(config_loader=lambda:{"provider":"ollama"},provider_factory=lambda config:FakeProvider(" "))
        self.assertEqual(report.status,"fail")

if __name__=="__main__": unittest.main()
