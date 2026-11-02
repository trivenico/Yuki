import json
import unittest

from yuki_cli import run_cli


class FakeProvider:
    def __init__(self, available=True, answer="hello"):
        self.available = available
        self.answer = answer
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.answer


class FakeReport:
    def to_dict(self):
        return {"operating_system": "TestOS", "capabilities": {}}


class YukiCliTests(unittest.TestCase):
    def test_conversation_preview_is_read_only_and_does_not_initialize_provider(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            export=Path(directory)/"history.jsonl"
            export.write_text('{"id":"m1","role":"user","content":"Hello Yuki"}\n',encoding="utf-8")
            output=[]
            def forbidden_provider_factory(config):
                raise AssertionError("preview must not initialize a provider")
            code=run_cli(
                ["conversation-preview",str(export),"--format","jsonl"],
                provider_factory=forbidden_provider_factory,
                output_fn=output.append,
            )
            self.assertEqual(code,0)
            result=json.loads(output[0])
            self.assertEqual(result["records_found"],1)
            self.assertFalse(result["stored"])
            self.assertTrue(result["consent_required_for_import"])

    def test_conversation_preview_rejects_missing_file(self):
        output=[]
        code=run_cli(
            ["conversation-preview","/path/that/does/not/exist.json","--format","chatgpt"],
            output_fn=output.append,
        )
        self.assertEqual(code,2)
        self.assertIn("not found",output[0].lower())
    def test_conversation_import_requires_confirmation_and_does_not_initialize_provider(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"export.json"
            store=Path(directory)/"memories.json"
            source.write_text(json.dumps({"messages":[{"id":"m1","role":"user","content":"Hello Yuki"},{"id":"m2","role":"assistant","content":"api_key=abc123"}]}),encoding="utf-8")
            output=[]
            forbidden=lambda config: self.fail("import must not initialize provider")
            args=["conversation-import",str(source),"--store",str(store)]
            self.assertEqual(run_cli(args,provider_factory=forbidden,output_fn=output.append),2)
            self.assertFalse(store.exists())
            self.assertEqual(run_cli(args+["--confirm-import"],provider_factory=forbidden,output_fn=output.append),0)
            result=json.loads(output[-1])
            self.assertTrue(result["imported"])
            self.assertEqual(result["message_count"],2)
            saved=json.loads(store.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]",saved[0]["messages"][1]["text"])
            self.assertEqual(saved[0]["training_status"],"not_eligible_pending_review")

    def test_conversation_import_deduplicates_repeated_export(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"export.json"
            store=Path(directory)/"memories.json"
            source.write_text(json.dumps({"messages":[{"role":"user","content":"One note"}]}),encoding="utf-8")
            output=[]
            args=["conversation-import",str(source),"--store",str(store),"--confirm-import"]
            self.assertEqual(run_cli(args,output_fn=output.append),0)
            self.assertEqual(run_cli(args,output_fn=output.append),0)
            self.assertTrue(json.loads(output[-1])["duplicate"])
    def test_ask_uses_configured_provider_and_returns_answer(self):
        provider = FakeProvider(answer="real answer")
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {"provider": "fake"},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, ["real answer"])
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("User message: hello", provider.prompts[0])
        self.assertIn("You are Yuki", provider.prompts[0])

    def test_provider_status_reports_unavailable_with_nonzero_exit(self):
        provider = FakeProvider(available=False)
        output = []
        code = run_cli(
            ["provider-status"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        report = json.loads(output[0])
        self.assertEqual(report["status"], "unavailable")

    def test_doctor_is_json_and_does_not_initialize_provider(self):
        output = []
        def forbidden_provider_factory(config):
            raise AssertionError("doctor must not initialize a provider")
        code = run_cli(
            ["doctor"],
            provider_factory=forbidden_provider_factory,
            readiness_builder=FakeReport,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output[0])["operating_system"], "TestOS")

    def test_ask_returns_nonzero_when_provider_generation_fails(self):
        class FailingProvider(FakeProvider):
            def generate(self, prompt):
                raise RuntimeError("private payload")

        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FailingProvider(),
            output_fn=output.append,
        )
        self.assertEqual(code, 4)
        self.assertNotIn("private payload", output[0])

    def test_ask_returns_unavailable_exit_code(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FakeProvider(available=False),
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        self.assertIn("unavailable", output[0].lower())

    def test_provider_initialization_failure_is_sanitized(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: (_ for _ in ()).throw(RuntimeError("secret payload")),
            output_fn=output.append,
        )
        self.assertEqual(code, 2)
        self.assertNotIn("secret payload", output[0])


if __name__ == "__main__":
    unittest.main()
