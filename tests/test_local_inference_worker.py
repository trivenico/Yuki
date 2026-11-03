import tempfile, unittest
from pathlib import Path
from unittest.mock import Mock
from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService
from automation.local_inference_worker import LocalInferenceWorker

class LocalWorkerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); root=Path(self.tmp.name)
  pair=DevicePairingService(root/"pair.sqlite"); c=pair.create_pairing_challenge("owner"); self.device=pair.pair_device(c.code,"worker")
  self.jobs=InferenceJobService(root/"jobs.sqlite",pair)
  self.provider=Mock(); self.provider.is_available.return_value=True
  self.provider.generate_chat.return_value={"text":"local answer"}
  self.worker=LocalInferenceWorker(self.jobs,self.device.token,{"local/model":self.provider})
 def submit(self,model="local/model",text="hello"):
  return self.jobs.create_job("owner",model_id=model,device_id=self.device.device.device_id,input_text=text)
 def test_idle_without_jobs(self):
  result=self.worker.run_once(); self.assertEqual(result.status,"idle"); self.assertFalse(result.claimed)
 def test_runs_allowlisted_model(self):
  job=self.submit(); result=self.worker.run_once()
  self.assertEqual(result.status,"succeeded"); self.assertEqual(self.jobs.get_job("owner",job.job_id).result_text,"local answer")
  self.provider.generate_chat.assert_called_once_with(messages=[{"role":"user","content":"hello"}],tools=[],tool_choice="none")
 def test_unlisted_model_fails_without_provider_call(self):
  job=self.submit("untrusted/model"); result=self.worker.run_once()
  self.assertEqual(result.error_code,"model_not_allowlisted"); self.provider.generate_chat.assert_not_called()
  self.assertEqual(self.jobs.get_job("owner",job.job_id).error_code,"model_not_allowlisted")
 def test_unavailable_model_fails_closed(self):
  job=self.submit(); self.provider.is_available.return_value=False
  self.assertEqual(self.worker.run_once().error_code,"model_unavailable")
  self.provider.generate_chat.assert_not_called()
 def test_provider_error_is_sanitized(self):
  job=self.submit(); self.provider.generate_chat.side_effect=RuntimeError("secret endpoint and prompt")
  result=self.worker.run_once(); self.assertEqual(result.error_code,"inference_failed")
  self.assertEqual(self.jobs.get_job("owner",job.job_id).error_code,"inference_failed")
 def test_output_is_bounded(self):
  job=self.submit(); worker=LocalInferenceWorker(self.jobs,self.device.token,{"local/model":self.provider},max_output_bytes=3)
  self.provider.generate_chat.return_value={"text":"too long"}
  self.assertEqual(worker.run_once().error_code,"output_too_large")
  self.assertEqual(self.jobs.get_job("owner",job.job_id).status,"failed")
 def test_provider_mapping_validated(self):
  with self.assertRaises(ValueError): LocalInferenceWorker(self.jobs,self.device.token,{})
  with self.assertRaises(TypeError): LocalInferenceWorker(self.jobs,self.device.token,{"m":object()})
if __name__=="__main__": unittest.main()
