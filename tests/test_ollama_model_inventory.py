import json
import unittest
from unittest.mock import patch
from providers.ollama_model_inventory import (
 InstalledModel, OllamaModelInventory, estimate_requirements, parse_model_inventory, assess_resource_fit,
)

class Response:
 def __init__(self,body): self.body=body
 def __enter__(self): return self
 def __exit__(self,*args): pass
 def read(self,n=-1): return self.body[:n]
class Opener:
 def __init__(self,body): self.body=body; self.request=None
 def open(self,request,timeout): self.request=request; return Response(self.body)

class OllamaInventoryTests(unittest.TestCase):
 def test_parse_inventory_and_metadata(self):
  models=parse_model_inventory({"models":[
   {"name":"qwen:7b","size":7_000_000_000,"digest":"abc","details":{"parameter_size":"7B","quantization_level":"Q4_K_M","family":"qwen"}},
   {"name":"llama:8b","size":8_000_000_000,"details":{}},
  ]})
  self.assertEqual([m.name for m in models],["llama:8b","qwen:7b"])
  self.assertEqual(models[1].quantization,"Q4_K_M")
 def test_reject_malformed_inventory(self):
  with self.assertRaises(ValueError): parse_model_inventory({"models":[{"name":"../x","size":100}]})
  with self.assertRaises(ValueError): parse_model_inventory({"models":[{"name":"x","size":True}]})
 def test_estimate_is_explicitly_advisory(self):
  model=InstalledModel("org/model",None,10_000,None,None,None)
  estimate=estimate_requirements(model,gpu_offload_ratio=.5)
  self.assertEqual(estimate["estimated_host_memory_bytes"],12_000)
  self.assertEqual(estimate["estimated_gpu_memory_bytes"],6_000)
  self.assertTrue(estimate["requires_runtime_measurement"])
  self.assertFalse(estimate["automatic_activation"])
 def test_inventory_client_reads_local_endpoint(self):
  opener=Opener(json.dumps({"models":[{"name":"tiny:latest","size":1000}]}).encode())
  client=OllamaModelInventory(opener=opener)
  result=client.list_installed()
  self.assertEqual(result[0].name,"tiny:latest")
  self.assertTrue(opener.request.full_url.endswith("/api/tags"))
 def test_reject_non_loopback_http(self):
  with self.assertRaises(ValueError): OllamaModelInventory("http://example.com:11434")
 def test_bound_response_size(self):
  opener=Opener(b"x"*2_000_001)
  with self.assertRaisesRegex(RuntimeError,"too large"):
   OllamaModelInventory(opener=opener).list_installed()
 def test_reject_invalid_estimate_parameters(self):
  model=InstalledModel("x",None,100,None,None,None)
  with self.assertRaises(ValueError): estimate_requirements(model,runtime_overhead_ratio=9)
  with self.assertRaises(ValueError): estimate_requirements(model,gpu_offload_ratio=2)


 def test_assess_resource_fit_requires_headroom(self):
  model=InstalledModel("tiny:latest",None,10_000,None,None,None)
  fit=assess_resource_fit(model,available_host_memory_bytes=14_000)
  self.assertEqual(fit["status"],"plausible_fit")
  self.assertEqual(fit["required_host_memory_with_headroom_bytes"],13_800)
  self.assertTrue(fit["requires_runtime_measurement"])
  self.assertFalse(fit["automatic_download"])
  self.assertFalse(fit["automatic_activation"])
 def test_assess_resource_fit_reports_insufficient_host_memory(self):
  model=InstalledModel("large:latest",None,10_000,None,None,None)
  fit=assess_resource_fit(model,available_host_memory_bytes=13_799)
  self.assertEqual(fit["status"],"host_memory_insufficient")
 def test_assess_resource_fit_does_not_assume_gpu_capacity(self):
  model=InstalledModel("gpu:latest",None,10_000,None,None,None)
  fit=assess_resource_fit(model,available_host_memory_bytes=20_000,gpu_offload_ratio=.5)
  self.assertEqual(fit["status"],"unknown_gpu_capacity")
 def test_assess_resource_fit_checks_supplied_gpu_capacity(self):
  model=InstalledModel("gpu:latest",None,10_000,None,None,None)
  fit=assess_resource_fit(model,available_host_memory_bytes=20_000,available_gpu_memory_bytes=5_000,gpu_offload_ratio=.5)
  self.assertEqual(fit["status"],"gpu_memory_insufficient")
 def test_assess_resource_fit_rejects_invalid_snapshots(self):
  model=InstalledModel("tiny:latest",None,10_000,None,None,None)
  with self.assertRaises(ValueError):
   assess_resource_fit(model,available_host_memory_bytes=True)
  with self.assertRaises(ValueError):
   assess_resource_fit(model,available_host_memory_bytes=10_000,required_headroom_ratio=2)

if __name__=="__main__": unittest.main()
