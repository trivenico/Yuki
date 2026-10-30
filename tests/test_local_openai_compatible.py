import json
import unittest
from unittest.mock import patch,Mock
from urllib.error import HTTPError

from providers.local_openai_compatible import LocalOpenAICompatibleProvider
from models.open_source_catalog import MODEL_FAMILIES,INFERENCE_RUNTIMES


class LocalOpenAICompatibleTests(unittest.TestCase):
    def test_only_loopback_endpoints_are_accepted(self):
        for url in ("http://127.0.0.1:8000/v1","http://localhost:8000/v1","https://[::1]:8000/v1"):
            self.assertIsInstance(LocalOpenAICompatibleProvider("test",base_url=url),LocalOpenAICompatibleProvider)
        for url in ("http://192.168.1.2:8000/v1","https://example.com/v1","http://user:pass@localhost/v1"):
            with self.assertRaises(ValueError):
                LocalOpenAICompatibleProvider("test",base_url=url)

    def test_rejects_invalid_limits(self):
        with self.assertRaises(ValueError): LocalOpenAICompatibleProvider("test",timeout=0)
        with self.assertRaises(ValueError): LocalOpenAICompatibleProvider("test",max_prompt_chars=0)

    @patch("providers.local_openai_compatible.urllib.request.build_opener")
    def test_generation_parses_openai_compatible_response(self,build):
        response=Mock()
        response.read.return_value=json.dumps({"choices":[{"message":{"content":" local answer "}}]}).encode()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        opener=Mock()
        opener.open.return_value=response
        build.return_value=opener
        provider=LocalOpenAICompatibleProvider("served-model")
        self.assertEqual(provider.generate("hello"),"local answer")
        self.assertTrue(opener.open.call_args.args[0].full_url.endswith("/chat/completions"))

    def test_prompt_size_is_bounded(self):
        provider=LocalOpenAICompatibleProvider("test",max_prompt_chars=3)
        with self.assertRaises(ValueError): provider.generate("four")

    def test_model_catalog_is_explicitly_review_required(self):
        self.assertGreaterEqual(len(MODEL_FAMILIES),6)
        for family in MODEL_FAMILIES:
            self.assertEqual(family["license_policy"],"verify_each_checkpoint")
        self.assertTrue(any(x["name"]=="vLLM" for x in INFERENCE_RUNTIMES))


if __name__=="__main__": unittest.main()
