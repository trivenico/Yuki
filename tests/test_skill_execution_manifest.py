import unittest
from dataclasses import replace
from core.sandbox_policy import SandboxPolicy
from core.skill_execution_manifest import SkillExecutionManifest,SkillManifestError
class Tests(unittest.TestCase):
 def setUp(self):
  self.m=SkillExecutionManifest(1,"skill","a"*64,"src/main.py",{"type":"object"},{"type":"object"},frozenset(),"registry.test/runtime@sha256:"+"b"*64,("python","/skill/main.py"),SandboxPolicy())
 def test_valid_and_stable(self):
  self.assertIs(self.m.validate(),self.m); self.assertEqual(self.m.manifest_sha256,self.m.manifest_sha256)
 def test_image_and_command_bound_into_hash(self):
  self.assertNotEqual(self.m.manifest_sha256,replace(self.m,command=("python","/other.py")).manifest_sha256)
  self.assertNotEqual(self.m.manifest_sha256,replace(self.m,image="registry.test/runtime@sha256:"+"c"*64).manifest_sha256)
 def test_mutable_image_rejected(self):
  with self.assertRaises(SkillManifestError): replace(self.m,image="python:latest").validate()
 def test_path_traversal_rejected(self):
  for p in ("../x","a/../../x","/tmp/x","C:/x","a\\x","a//x"):
   with self.subTest(p=p),self.assertRaises(SkillManifestError): replace(self.m,entrypoint=p).validate()
 def test_bad_digest_and_version_rejected(self):
  with self.assertRaises(SkillManifestError): replace(self.m,artifact_sha256="A"*64).validate()
  with self.assertRaises(SkillManifestError): replace(self.m,manifest_version=True).validate()
 def test_capability_mismatch_rejected(self):
  with self.assertRaises(SkillManifestError): replace(self.m,sandbox_policy=SandboxPolicy(capabilities=frozenset({"write_workspace"}))).validate()
 def test_external_schema_ref_rejected(self):
  with self.assertRaises(SkillManifestError): replace(self.m,input_schema={"type":"object","$ref":"https://evil"}).validate()
 def test_schema_size_rejected(self):
  with self.assertRaises(SkillManifestError): replace(self.m,input_schema={"type":"object","description":"x"*20000}).validate()
 def test_argv_bounds(self):
  with self.assertRaises(SkillManifestError): replace(self.m,command=("python","bad\x00arg")).validate()
if __name__=="__main__": unittest.main()
