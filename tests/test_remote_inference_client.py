import json
import unittest
from unittest.mock import patch
from automation.remote_inference_client import RemoteInferenceJobClient

class FakeResponse:
 def __init__(self,body): self.body=body
 def __enter__(self): return self
 def __exit__(self,*args): pass
 def read(self,n=-1): return self.body[:n]
class FakeOpener:
 def __init__(self,body): self.body=body; self.request=None
 def open(self,request,timeout): self.request=request; return FakeResponse(self.body)

class RemoteClientTests(unittest.TestCase):
 def setUp(self): self.client=RemoteInferenceJobClient("https://yuki.example","x"*32)
 def test_claim_parses_job_and_sends_device_auth(self):
  opener=FakeOpener(json.dumps({"job":{"job_id":"a"*32,"model_id":"local/model","input_text":"hello","lease_expires_at":"2030-01-01T00:00:00+00:00","claim_generation":2,"claim_token":"c"*43}}).encode())
  with patch("urllib.request.build_opener",return_value=opener): job=self.client.claim_next()
  self.assertEqual(job.input_text,"hello"); self.assertEqual(opener.request.get_header("Authorization"),"Bearer "+"x"*32)
 def test_idle_claim(self):
  with patch("urllib.request.build_opener",return_value=FakeOpener(b'{"job":null}')): self.assertIsNone(self.client.claim_next())
 def test_renew_lease_sends_claim_fencing(self):
  opener=FakeOpener(b'{"lease_expires_at":"2030-01-01T00:02:00+00:00"}')
  with patch("urllib.request.build_opener",return_value=opener):
   expiry=self.client.renew_job_lease("x"*32,"a"*32,claim_generation=2,claim_token="c"*43)
  self.assertEqual(expiry,"2030-01-01T00:02:00+00:00")
  self.assertEqual(json.loads(opener.request.data),{"claim_generation":2,"claim_token":"c"*43})
 def test_complete_sends_claim_fencing_and_result(self):
  opener=FakeOpener(b'{"job":{"status":"succeeded"}}')
  with patch("urllib.request.build_opener",return_value=opener):
   self.client.complete_job("x"*32,"a"*32,claim_generation=2,claim_token="c"*43,result_text="ok")
  self.assertEqual(json.loads(opener.request.data),{"claim_generation":2,"claim_token":"c"*43,"result_text":"ok"})
 def test_reject_insecure_remote_endpoint(self):
  with self.assertRaisesRegex(ValueError,"HTTPS"): RemoteInferenceJobClient("http://remote.example","x"*32)
 def test_response_limit(self):
  client=RemoteInferenceJobClient("https://yuki.example","x"*32,max_response_bytes=1024)
  with patch("urllib.request.build_opener",return_value=FakeOpener(b"x"*1025)):
   with self.assertRaises(RuntimeError): client.claim_next()
if __name__=="__main__": unittest.main()
