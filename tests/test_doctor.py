import unittest
from core.doctor import run_doctor

class FakeProvider:
    def __init__(self, available=True): self.available=available
    def generate(self, prompt): return "answer"
    def is_available(self): return self.available

class RuntimeDoctorTests(unittest.TestCase):
    def test_available_provider_is_reported_without_claiming_inference(self):
        report=run_doctor(config_loader=lambda:{"provider":"fake"},provider_factory=lambda config, environ:FakeProvider(),environ={})
        self.assertTrue(report.ready_for_basic_chat)
        self.assertEqual(next(x for x in report.checks if x.name=="inference_round_trip").status,"not_run")
    def test_unavailable_provider_fails_readiness(self):
        report=run_doctor(config_loader=lambda:{"provider":"fake"},provider_factory=lambda config, environ:FakeProvider(False),environ={})
        self.assertFalse(report.ready_for_basic_chat)
    def test_config_error_does_not_leak_exception_message(self):
        def fail(): raise RuntimeError("secret=/private/path/token=do-not-print")
        report=run_doctor(config_loader=fail,provider_factory=lambda *args,**kwargs:None,environ={})
        rendered=" ".join(x.detail for x in report.checks)
        self.assertNotIn("do-not-print",rendered)
        self.assertFalse(report.ready_for_basic_chat)
    def test_invalid_adapter_contract_fails_closed(self):
        report=run_doctor(config_loader=lambda:{"provider":"bad"},provider_factory=lambda *args,**kwargs:object(),environ={})
        self.assertFalse(report.ready_for_basic_chat)
        self.assertIn("provider_adapter",[x.name for x in report.checks])

if __name__=="__main__": unittest.main()
