import tempfile, unittest, time
from pathlib import Path
from threading import Event
from dataclasses import replace
from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService
from providers.ollama import OllamaProvider
from automation.local_inference_worker import LocalInferenceWorker

class FakeOllamaProvider(OllamaProvider):
 def __init__(self, timeout=45):
  super().__init__(model="test-model",timeout=timeout)
  self.available=True; self.response={"text":"local answer"}; self.error=None; self.calls=[]
 def is_available(self): return self.available
 def generate_chat(self,**kwargs):
  self.calls.append(kwargs)
  if self.error: raise self.error
  return self.response

class LocalWorkerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); root=Path(self.tmp.name)
  pair=DevicePairingService(root/"pair.sqlite"); challenge=pair.create_pairing_challenge("owner"); self.device=pair.pair_device(challenge.code,"worker")
  self.jobs=InferenceJobService(root/"jobs.sqlite",pair); self.provider=FakeOllamaProvider()
  self.worker=LocalInferenceWorker(self.jobs,self.device.token,{"local/model":self.provider})
 def submit(self,model="local/model",text="hello"):
  return self.jobs.create_job("owner",model_id=model,device_id=self.device.device.device_id,input_text=text)
 def test_idle_without_jobs(self):
  result=self.worker.run_once(); self.assertEqual(result.status,"idle"); self.assertFalse(result.claimed)
 def test_runs_allowlisted_model(self):
  job=self.submit(); result=self.worker.run_once()
  self.assertEqual(result.status,"succeeded"); self.assertEqual(self.jobs.get_job("owner",job.job_id).result_text,"local answer")
  self.assertEqual(self.provider.calls,[{"messages":[{"role":"user","content":"hello"}],"tools":[],"tool_choice":"none"}])
 def test_unlisted_model_fails_without_provider_call(self):
  job=self.submit("untrusted/model"); result=self.worker.run_once()
  self.assertEqual(result.error_code,"model_not_allowlisted"); self.assertEqual(self.provider.calls,[])
  self.assertEqual(self.jobs.get_job("owner",job.job_id).error_code,"model_not_allowlisted")
 def test_unavailable_model_fails_closed(self):
  job=self.submit(); self.provider.available=False
  self.assertEqual(self.worker.run_once().error_code,"model_unavailable"); self.assertEqual(self.provider.calls,[])
 def test_provider_error_is_sanitized(self):
  job=self.submit(); self.provider.error=RuntimeError("secret endpoint and prompt")
  result=self.worker.run_once(); self.assertEqual(result.error_code,"inference_failed")
  self.assertEqual(self.jobs.get_job("owner",job.job_id).error_code,"inference_failed")
 def test_output_is_bounded(self):
  job=self.submit(); worker=LocalInferenceWorker(self.jobs,self.device.token,{"local/model":self.provider},max_output_bytes=3)
  self.provider.response={"text":"too long"}
  self.assertEqual(worker.run_once().error_code,"output_too_large")
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"failed")
 def test_lease_must_exceed_provider_timeout(self):
  class SlowProvider(FakeOllamaProvider):
   def __init__(self): super().__init__(timeout=120)
  with self.assertRaisesRegex(ValueError,"job lease must exceed Ollama timeout"):
   LocalInferenceWorker(self.jobs,self.device.token,{"local/model":SlowProvider()})
 def test_stale_claim_token_cannot_complete_job(self):
  job=self.submit()
  class StaleClaimService:
   lease_seconds=self.jobs.lease_seconds
   def claim_next(inner, token):
    claimed=self.jobs.claim_next(token)
    return replace(claimed,claim_token="z"*32) if claimed else None
   def complete_job(inner,*args,**kwargs):
    return self.jobs.complete_job(*args,**kwargs)
   def renew_job_lease(inner,*args,**kwargs):
    return self.jobs.renew_job_lease(*args,**kwargs)
  worker=LocalInferenceWorker(StaleClaimService(),self.device.token,{"local/model":self.provider})
  result=worker.run_once()
  self.assertEqual(result.status,"failed")
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"running")
  self.assertIsNone(self.jobs.get_job("owner",job.job_id).result_text)

 def test_long_inference_renews_claim(self):
  root=Path(self.tmp.name)
  pair=DevicePairingService(root/"pair-long.sqlite")
  challenge=pair.create_pairing_challenge("owner")
  device=pair.pair_device(challenge.code,"long-worker")
  jobs=InferenceJobService(root/"jobs-long.sqlite",pair,lease_seconds=15)
  jobs.create_job("owner",model_id="local/model",device_id=device.device.device_id,input_text="hello")
  entered=Event()
  class SlowProvider(FakeOllamaProvider):
   def __init__(self): super().__init__(timeout=1)
   def generate_chat(self,**kwargs):
    entered.set()
    time.sleep(5.5)
    return {"text":"renewed answer"}
  provider=SlowProvider()
  worker=LocalInferenceWorker(jobs,device.token,{"local/model":provider})
  result=worker.run_once()
  self.assertTrue(entered.is_set())
  self.assertEqual(result.status,"succeeded")
  self.assertEqual(jobs.list_jobs("owner")[0].result_text,"renewed answer")

 def test_lost_lease_prevents_result_commit(self):
  job=self.submit()
  class StaleClaimService:
   lease_seconds=self.jobs.lease_seconds
   def claim_next(inner, token):
    claimed=self.jobs.claim_next(token)
    return replace(claimed,claim_token="z"*32) if claimed else None
   def complete_job(inner,*args,**kwargs):
    return self.jobs.complete_job(*args,**kwargs)
   def renew_job_lease(inner,*args,**kwargs):
    return self.jobs.renew_job_lease(*args,**kwargs)
  class SlowProvider(FakeOllamaProvider):
   def generate_chat(inner,**kwargs):
    time.sleep(10.5)
    return {"text":"must not commit"}
  worker=LocalInferenceWorker(StaleClaimService(),self.device.token,{"local/model":SlowProvider()})
  result=worker.run_once()
  self.assertEqual(result.error_code,"lease_lost")
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"running")
  self.assertIsNone(self.jobs.get_job("owner",job.job_id).result_text)

 def test_provider_mapping_validated(self):
  with self.assertRaises(ValueError): LocalInferenceWorker(self.jobs,self.device.token,{})
  with self.assertRaises(TypeError): LocalInferenceWorker(self.jobs,self.device.token,{"m":object()})
if __name__=="__main__": unittest.main()
