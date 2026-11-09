import threading
import unittest
from types import SimpleNamespace
from automation.inference_worker_supervisor import InferenceWorkerSupervisor

class FakeWorker:
 def __init__(self,results): self.results=list(results); self.calls=0
 def run_once(self):
  self.calls+=1
  value=self.results.pop(0) if self.results else SimpleNamespace(claimed=False,status="idle")
  if isinstance(value,BaseException): raise value
  return value

class SupervisorTests(unittest.TestCase):
 def test_processes_success_failure_then_stop(self):
  stop=threading.Event()
  worker=FakeWorker([SimpleNamespace(claimed=True,status="succeeded"),SimpleNamespace(claimed=True,status="failed")])
  def wait(_): stop.set()
  stats=InferenceWorkerSupervisor(worker,stop_event=stop,wait=wait).run_forever()
  self.assertEqual((stats.claimed,stats.succeeded,stats.failed),(2,1,1))
  self.assertEqual(stats.stopped_reason,"stop_requested")
 def test_auth_failure_stops_immediately(self):
  worker=FakeWorker([PermissionError("secret")])
  stats=InferenceWorkerSupervisor(worker,wait=lambda _:None).run_forever()
  self.assertEqual(stats.stopped_reason,"device_authentication_failed")
  self.assertEqual(stats.transient_errors,0)
 def test_transient_errors_use_bounded_retries(self):
  worker=FakeWorker([RuntimeError("network")]*3)
  delays=[]
  stats=InferenceWorkerSupervisor(worker,max_consecutive_errors=3,
      error_backoff_initial=.1,error_backoff_max=.15,wait=delays.append).run_forever()
  self.assertEqual(stats.stopped_reason,"consecutive_error_limit")
  self.assertEqual(delays,[.1,.15])
  self.assertEqual(stats.transient_errors,3)
 def test_idle_waits_until_requested_stop(self):
  stop=threading.Event(); worker=FakeWorker([])
  def wait(_): stop.set()
  stats=InferenceWorkerSupervisor(worker,stop_event=stop,wait=wait).run_forever()
  self.assertEqual(stats.idle_polls,1)
 def test_reject_invalid_backoff(self):
  with self.assertRaises(ValueError):
   InferenceWorkerSupervisor(FakeWorker([]),error_backoff_initial=2,error_backoff_max=1)
if __name__=="__main__": unittest.main()
