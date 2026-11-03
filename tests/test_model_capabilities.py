import unittest
from core.model_capabilities import (
 CAPABILITIES, ModelCapability, ModelCapabilityRegistry,
 classify_task, recommend_capable_models,
)

class ModelCapabilityTests(unittest.TestCase):
 def setUp(self):
  self.registry=ModelCapabilityRegistry([
   ModelCapability("org/code-model",frozenset({"coding","reasoning"}),"local benchmark v1",True,"verified"),
   ModelCapability("org/vision-model",frozenset({"vision","reasoning"}),"vision eval v2",True,"verified"),
   ModelCapability("org/unverified",frozenset({"coding"}),"unreviewed card",False,"unknown"),
   ModelCapability("org/unknown-runtime",frozenset({"coding"}),"reviewed card",True,"unknown"),
  ])
 def test_classifies_coding(self):
  result=classify_task("Please debug this Python function")
  self.assertIn("coding",result["required_capabilities"])
  self.assertFalse(result["automatic_execution"])
 def test_multicapability_task(self):
  result=classify_task("Inspect this screenshot and fix the code shown")
  self.assertIn("vision",result["required_capabilities"])
  self.assertIn("coding",result["required_capabilities"])
 def test_ambiguous_task_requests_clarification(self):
  result=classify_task("Can you help me with this?")
  self.assertTrue(result["needs_clarification"])
  self.assertEqual(result["confidence"],.35)
 def test_explicit_type_overrides_heuristics(self):
  result=classify_task("debug this code",explicit_task_type="reasoning")
  self.assertEqual(result["required_capabilities"],["reasoning"])
  with self.assertRaises(ValueError): classify_task("hello",explicit_task_type="unknown")
 def test_only_verified_runtime_compatible_models_recommended(self):
  classification=classify_task("debug this Python function")
  result=recommend_capable_models(classification,self.registry)
  self.assertEqual([x["model_id"] for x in result["candidates"]],["org/code-model"])
  self.assertFalse(result["automatic_activation"])
 def test_combined_capability_requires_one_model_with_both(self):
  classification=classify_task("Inspect this screenshot and fix the code shown")
  result=recommend_capable_models(classification,self.registry)
  self.assertEqual(result["candidates"],[])
 def test_reject_invalid_capability_claims(self):
  with self.assertRaises(ValueError):
   ModelCapability("org/model",frozenset({"made_up"}),"evidence",True,"verified")
  with self.assertRaises(ValueError):
   ModelCapability("../bad",frozenset({"coding"}),"evidence")
 def test_registry_rejects_unreviewed_required_filter(self):
  with self.assertRaises(ValueError): self.registry.find({"unknown"})

if __name__=="__main__": unittest.main()
