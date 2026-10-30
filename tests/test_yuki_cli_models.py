import json
import unittest
from yuki_cli import run_cli

class CliModelCommandsTests(unittest.TestCase):
    def test_hardware_command_does_not_initialize_provider(self):
        output=[]
        code=run_cli(["hardware"],provider_factory=lambda _:self.fail("provider must not initialize"),hardware_reporter=lambda:{"ram_total_bytes":10},output_fn=output.append)
        self.assertEqual(code,0); self.assertEqual(json.loads(output[0])["ram_total_bytes"],10)
    def test_models_command_uses_shared_catalog_and_task_filter(self):
        output=[]
        code=run_cli(["models","--task","coding"],model_catalog=lambda task:{"task":task,"families":[]},provider_factory=lambda _:self.fail("provider must not initialize"),output_fn=output.append)
        self.assertEqual(code,0); self.assertEqual(json.loads(output[0])["task"],"coding")
    def test_models_command_sanitizes_service_errors(self):
        output=[]
        code=run_cli(["models"],model_catalog=lambda **_:(_ for _ in ()).throw(RuntimeError("secret")),provider_factory=lambda _:self.fail("provider must not initialize"),output_fn=output.append)
        self.assertEqual(code,4); self.assertNotIn("secret",output[0])

if __name__=="__main__": unittest.main()
