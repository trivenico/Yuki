import io
import json
import unittest
from unittest.mock import patch
from automation.remote_inference_client import RemoteInferenceJobClient
from automation.local_inference_worker import LocalInferenceWorker

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
        opener=FakeOpener(json.dumps({"job":{"job_id":"a"*32,"model_id":"local/model","input_text":"hello","lease_expires_at":"2030-01-01T00:00:00+00:00","lease_token":"t"*43}}).encode())
        with patch("urllib.request.build_opener",return_value=opener):
            job=self.client.claim_next()
        self.assertEqual(job.input_text,"hello")
        self.assertEqual(job.lease_token,"t"*43)
        self.assertEqual(opener.request.get_header("Authorization"),"Bearer "+"x"*32)
        self.assertTrue(opener.request.full_url.endswith("/v1/inference/claim"))
    def test_idle_claim(self):
        with patch("urllib.request.build_opener",return_value=FakeOpener(b'{"job":null}')):
            self.assertIsNone(self.client.claim_next())
    def test_complete_sends_only_result(self):
        opener=FakeOpener(b'{"job":{"status":"succeeded"}}')
        with patch("urllib.request.build_opener",return_value=opener):
            self.client.complete_job("x"*32,"a"*32,lease_token="t"*43,result_text="ok")
        self.assertEqual(json.loads(opener.request.data),{"lease_token":"t"*43,"result_text":"ok"})
    def test_reject_insecure_remote_endpoint(self):
        with self.assertRaisesRegex(ValueError,"HTTPS"):
            RemoteInferenceJobClient("http://remote.example","x"*32)
    def test_reject_redirects_and_response_overflow(self):
        client=RemoteInferenceJobClient("https://yuki.example","x"*32,max_response_bytes=1024)
        with patch("urllib.request.build_opener",return_value=FakeOpener(b"x"*1025)):
            with self.assertRaises(RuntimeError): client.claim_next()
    def test_worker_accepts_transport_protocol(self):
        class Transport:
            lease_seconds=120
            def claim_next(self,token): return None
            def complete_job(self,*args,**kwargs): pass
        class Provider:
            timeout=45
        with self.assertRaisesRegex(TypeError,"OllamaProvider"):
            LocalInferenceWorker(Transport(),"x"*32,{"model":Provider()})

if __name__=="__main__": unittest.main()
