import tempfile, unittest
from pathlib import Path
from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService
from providers.ollama import OllamaProvider
from automation.local_inference_worker import LocalInferenceWorker
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway

class FakeOllamaProvider(OllamaProvider):
 def __init__(self):
  super().__init__(model="test-model",timeout=45)
  self.available=True; self.response={"text":"local answer"}; self.error=None; self.calls=[]
 def is_available(self): return self.available
 def generate_chat(self,**kwargs):
  self.calls.append(kwargs)
  if self.error: raise self.error
  return self.response

class LocalWorkerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); root=Path(self.tmp.name)
  pair=DevicePairingService(root/"yuki.sqlite"); challenge=pair.create_pairing_challenge("owner"); self.device=pair.pair_device(challenge.code,"worker")
  self.jobs=InferenceJobService(root/"yuki.sqlite",pair); self.provider=FakeOllamaProvider()
  class Policy:
   def __init__(inner): inner.requests=[]; inner.allowed=True
   def authorize(inner,request):
    inner.requests.append(request)
    return AuthorizationDecision(inner.allowed,"decision","policy-v1","allowed" if inner.allowed else "denied")
  self.policy=Policy(); self.audit_events=[]
  self.gateway=AuthorizationGateway(self.policy,audit=lambda request,decision:self.audit_events.append((request.action,decision.allowed)))
  self.worker=self.make_worker()
 def make_worker(self, **kwargs):
  return LocalInferenceWorker(
   self.jobs,self.device.token,{"local/model":self.provider},
   authorization_gateway=kwargs.pop("authorization_gateway",self.gateway),
   principal_for_device=kwargs.pop("principal_for_device",lambda device: Principal(
    device.device_id,"workload",tenant_id=device.owner_id,agent_id="inference-worker",
    permissions=frozenset({"inference:claim","inference:execute"}),
   )),
   **kwargs,
  )
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
  job=self.submit(); worker=self.make_worker(max_output_bytes=3)
  self.provider.response={"text":"too long"}
  self.assertEqual(worker.run_once().error_code,"output_too_large")
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"failed")
 def test_lease_must_outlast_provider_timeout(self):
  class SlowProvider(FakeOllamaProvider):
   def __init__(self): super().__init__(); self.timeout=120
  with self.assertRaisesRegex(ValueError,"job_lease_must_exceed_provider_timeout_by_15_seconds"):
   LocalInferenceWorker(
    self.jobs,self.device.token,{"local/model":SlowProvider()},
    authorization_gateway=self.gateway,
    principal_for_device=lambda device: Principal(
     device.device_id,"workload",tenant_id=device.owner_id,agent_id="inference-worker",
     permissions=frozenset({"inference:claim","inference:execute"}),
    ),
   )
 def test_provider_mapping_validated(self):
  with self.assertRaises(ValueError):
   LocalInferenceWorker(
    self.jobs,self.device.token,{},authorization_gateway=self.gateway,
    principal_for_device=lambda device: Principal(device.device_id,"workload",tenant_id=device.owner_id,agent_id="inference-worker"),
   )
  with self.assertRaises(TypeError):
   LocalInferenceWorker(
    self.jobs,self.device.token,{"m":object()},authorization_gateway=self.gateway,
    principal_for_device=lambda device: Principal(device.device_id,"workload",tenant_id=device.owner_id,agent_id="inference-worker"),
   )
 def test_revocation_during_generation_prevents_completion(self):
  job=self.submit()
  original=self.provider.generate_chat
  def revoke_then_generate(**kwargs):
   self.jobs.pairing.revoke_device("owner",self.device.device.device_id)
   return original(**kwargs)
  self.provider.generate_chat=revoke_then_generate
  with self.assertRaises(PermissionError):
   self.worker.run_once()
  persisted=self.jobs.get_job("owner",job.job_id)
  self.assertEqual(persisted.status,"running")
  self.assertIsNone(persisted.result_text)

 def test_claim_and_provider_invocation_are_authorized_and_audited(self):
  job=self.submit(); result=self.worker.run_once()
  self.assertEqual(result.status,"succeeded")
  self.assertEqual([r.action for r in self.policy.requests],["inference:claim","inference:execute","inference:complete"])
  self.assertEqual(self.policy.requests[-1].task_id,job.job_id)
  self.assertTrue(self.policy.requests[-2].resource.startswith("inference-request:sha256:"))
  self.assertNotIn("hello",self.policy.requests[-1].resource)
  self.assertEqual(len(self.audit_events),3)
 def test_claim_denial_prevents_job_consumption(self):
  job=self.submit(); self.policy.allowed=False
  with self.assertRaises(PermissionError): self.worker.run_once()
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"queued")
  self.assertEqual(self.provider.calls,[])
 def test_execution_denial_prevents_provider_call_and_fails_job(self):
  job=self.submit()
  original=self.policy.authorize
  def deny_execution(request):
   if request.action=="inference:execute":
    return AuthorizationDecision(False,"decision","policy-v1","denied")
   return original(request)
  self.policy.authorize=deny_execution
  result=self.worker.run_once()
  self.assertEqual(result.error_code,"inference_not_authorized")
  self.assertEqual(self.provider.calls,[])
  self.assertEqual(self.jobs.get_job("owner",job.job_id).error_code,"inference_not_authorized")

 def test_completion_denial_prevents_result_persistence(self):
  job=self.submit()
  original=self.policy.authorize
  def deny_completion(request):
   if request.action=="inference:complete":
    return AuthorizationDecision(False,"decision","policy-v1","denied")
   return original(request)
  self.policy.authorize=deny_completion
  with self.assertRaises(PermissionError):
   self.worker.run_once()
  persisted=self.jobs.get_job("owner",job.job_id)
  self.assertEqual(persisted.status,"running")
  self.assertIsNone(persisted.result_text)
  self.assertEqual(len(self.provider.calls),1)

 def test_audit_failure_blocks_claim_and_provider_execution(self):
  job=self.submit()
  worker=self.make_worker(authorization_gateway=AuthorizationGateway(self.policy,audit=lambda *_:False))
  with self.assertRaises(PermissionError): worker.run_once()
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"queued")
  self.assertEqual(self.provider.calls,[])
 def test_invalid_device_principal_cannot_claim_jobs(self):
  job=self.submit()
  worker=self.make_worker(principal_for_device=lambda device: Principal("wrong-device","workload",tenant_id="owner",agent_id="worker"))
  with self.assertRaises(PermissionError): worker.run_once()
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"queued")
 def test_authorization_dependencies_are_mandatory(self):
  with self.assertRaises(TypeError): LocalInferenceWorker(self.jobs,self.device.token,{"local/model":self.provider})

if __name__=="__main__": unittest.main()
