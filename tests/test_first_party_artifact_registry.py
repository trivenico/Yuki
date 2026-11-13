import hashlib, json, tempfile, unittest
from pathlib import Path
from core.model_registry import ModelRegistry, ModelRegistryError

class FirstPartyArtifactRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        (self.root/"artifacts").mkdir()
        self.files={"weights.bin":b"weights","tokenizer.json":b"{}","config.json":b"{}"}
        for name,body in self.files.items(): (self.root/"artifacts"/name).write_bytes(body)
        self.data={"schema_version":1,"model_id":"yuki-core","version":"0.1.0","family":"language",
          "architecture":"yuki_decoder_v1","origin":"random_init","license":"Yuki project license; dataset licenses reviewed separately.",
          "training_data":"Curated Yuki-owned corpus with a separately versioned dataset manifest.",
          "tasks":["conversation","planning"],"artifacts":{
          key:{"path":f"artifacts/{name}","sha256":hashlib.sha256(self.files[name]).hexdigest()}
          for key,name in [("weights","weights.bin"),("tokenizer","tokenizer.json"),("config","config.json")]}}
        self.write()
    def tearDown(self): self.tmp.cleanup()
    def write(self): (self.root/"yuki-core.json").write_text(json.dumps(self.data),encoding="utf-8")
    def test_valid_manifest_and_hashes(self):
        loaded=ModelRegistry(self.root).load("yuki-core.json")
        self.assertTrue(loaded["verified"]); self.assertEqual(loaded["origin"],"random_init")
        self.assertEqual(set(loaded["artifacts"]),{"weights","tokenizer","config"})
    def test_rejects_external_checkpoint_origin(self):
        self.data["origin"]="huggingface_base_model"; self.write()
        with self.assertRaises(ModelRegistryError): ModelRegistry(self.root).load("yuki-core.json")
    def test_rejects_tampered_artifact(self):
        (self.root/"artifacts/weights.bin").write_bytes(b"tampered")
        with self.assertRaisesRegex(ModelRegistryError,"integrity"): ModelRegistry(self.root).load("yuki-core.json")
    def test_rejects_path_traversal(self):
        self.data["artifacts"]["weights"]["path"]="../outside.bin"; self.write()
        with self.assertRaises(ModelRegistryError): ModelRegistry(self.root).load("yuki-core.json")
    def test_rejects_symlink_escape(self):
        outside=self.root.parent/"yuki-outside-artifact.bin"; outside.write_bytes(b"outside")
        try:
            (self.root/"artifacts/escape.bin").symlink_to(outside)
            self.data["artifacts"]["weights"]["path"]="artifacts/escape.bin"
            self.data["artifacts"]["weights"]["sha256"]=hashlib.sha256(b"outside").hexdigest(); self.write()
            with self.assertRaises(ModelRegistryError): ModelRegistry(self.root).load("yuki-core.json")
        finally:
            outside.unlink(missing_ok=True)
    def test_rejects_non_string_family_and_boolean_schema(self):
        for key,value in [("family",[]),("schema_version",True)]:
            original=self.data[key]; self.data[key]=value; self.write()
            with self.assertRaises(ModelRegistryError): ModelRegistry(self.root).load("yuki-core.json")
            self.data[key]=original
    def test_rejects_unknown_manifest_fields(self):
        self.data["download_url"]="https://example.invalid/model"; self.write()
        with self.assertRaises(ModelRegistryError): ModelRegistry(self.root).load("yuki-core.json")

if __name__=="__main__": unittest.main()
