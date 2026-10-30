import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers.huggingface_download import download_model_snapshot, ModelDownloadError
from providers.huggingface_local import HuggingFaceLocalProvider
from providers.factory import create_provider
from providers.catalog import build_provider_catalog

class HuggingFaceLocalTests(unittest.TestCase):
    def test_local_provider_requires_config_and_safetensors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/"config.json").write_text("{}")
            with self.assertRaisesRegex(ValueError,"safetensors"):
                HuggingFaceLocalProvider(root)
            (root/"model.safetensors").write_bytes(b"")
            provider=HuggingFaceLocalProvider(root)
            self.assertEqual(provider.model_path,root.resolve())
    def test_provider_rejects_bad_options_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/"config.json").write_text("{}"); (root/"model.safetensors").write_bytes(b"")
            with self.assertRaises(ValueError): HuggingFaceLocalProvider(root,device="remote")
            with self.assertRaises(ValueError): HuggingFaceLocalProvider(root,max_new_tokens=99999)
            with self.assertRaises(ValueError): HuggingFaceLocalProvider(root).generate(" ")
    def test_factory_exposes_local_provider_without_loading_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/"config.json").write_text("{}"); (root/"model.safetensors").write_bytes(b"")
            provider=create_provider({"provider":"huggingface_local","hf_local_model_path":str(root)},environ={})
            self.assertIsInstance(provider,HuggingFaceLocalProvider)
            self.assertFalse(provider._model)
    def test_catalog_reports_local_runtime_without_claiming_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=build_provider_catalog(config={"provider":"huggingface_local","hf_local_model_path":tmp},environ={})
            item=next(x for x in result if x["name"]=="huggingface_local")
            self.assertTrue(item["ready"])
            self.assertEqual(item["status"],"local_checkpoint_configured_runtime_not_probed")
    def test_download_rejects_mutable_revision_before_optional_import(self):
        with self.assertRaisesRegex(ValueError,"pinned"):
            download_model_snapshot("org/model","main","/tmp/cache")
        with self.assertRaises(ValueError):
            download_model_snapshot("../bad","a"*40,"/tmp/cache")
    def test_download_rejects_invalid_budget(self):
        with self.assertRaises(ValueError):
            download_model_snapshot("org/model","a"*40,"/tmp/cache",max_bytes=0)

if __name__=="__main__": unittest.main()
