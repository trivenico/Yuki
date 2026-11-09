import tempfile,unittest,sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime,timedelta,timezone
from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService
class JobsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); root=Path(self.tmp.name)
  self.pair=DevicePairingService(root/"pair.sqlite"); self.jobs=InferenceJobService(root/"jobs.sqlite",self.pair,lease_seconds=30)
  c=self.pair.create_pairing_challenge("owner-a"); self.dev=self.pair.pair_device(c.code,"Worker")
  c=self.pair.create_pairing_challenge("owner-b"); self.other=self.pair.pair_device(c.code,"Other")
 def create(self,**kw):
  d=dict(owner_id="owner-a",model_id="local/model",device_id=self.dev.device.device_id,input_text="Hello"); d.update(kw); return self.jobs.create_job(**d)
 def test_owner_and_input_redaction(self):
  j=self.create(); self.assertIsNone(self.jobs.get_job("owner-b",j.job_id)); self.assertNotIn("input_text",j.public_dict())
 def test_assigned_device_only(self):
  self.create(); self.assertIsNone(self.jobs.claim_next(self.other.token)); self.assertEqual(self.jobs.claim_next(self.dev.token).input_text,"Hello")
 def test_complete_and_read(self):
  j=self.create(); claim=self.jobs.claim_next(self.dev.token); done=self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token,result_text="Hi"); self.assertEqual(done.status,"succeeded"); self.assertEqual(self.jobs.get_job("owner-a",j.job_id).result_text,"Hi")
 def test_wrong_device(self):
  j=self.create(); claim=self.jobs.claim_next(self.dev.token)
  with self.assertRaises(LookupError): self.jobs.complete_job(self.other.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token,result_text="bad")
 def test_cancel(self):
  j=self.create(); self.assertTrue(self.jobs.cancel_job("owner-a",j.job_id)); self.assertFalse(self.jobs.cancel_job("owner-a",j.job_id)); self.assertIsNone(self.jobs.claim_next(self.dev.token))
 def test_expired_lease_is_terminal_and_never_requeued(self):
  j=self.create(); self.jobs.claim_next(self.dev.token)
  with self.jobs._connect() as db:
   db.execute("UPDATE inference_jobs SET lease_expires_at=? WHERE job_id=?",
    ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),j.job_id))
  self.assertIsNone(self.jobs.claim_next(self.dev.token))
  stored=self.jobs.get_job("owner-a",j.job_id)
  self.assertEqual(stored.status,"failed")
  self.assertEqual(stored.error_code,"lease_expired")
  self.assertIsNone(stored.lease_expires_at)
 def test_revocation(self):
  self.create(); self.pair.revoke_device("owner-a",self.dev.device.device_id)
  with self.assertRaises(PermissionError): self.jobs.claim_next(self.dev.token)


 def test_cancel_invalidates_active_claim(self):
  j=self.create(); claim=self.jobs.claim_next(self.dev.token)
  self.assertTrue(self.jobs.cancel_job("owner-a",j.job_id))
  with self.assertRaises(ValueError):
   self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token,result_text="late")
 def test_expired_claim_cannot_complete(self):
  j=self.create(); claim=self.jobs.claim_next(self.dev.token)
  with self.jobs._connect() as db:
   db.execute("UPDATE inference_jobs SET lease_expires_at=? WHERE job_id=?",
    ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),j.job_id))
  with self.assertRaises(ValueError):
   self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token,result_text="late")
  self.assertEqual(self.jobs.get_job("owner-a",j.job_id).status,"running")

 def test_claim_token_is_private_and_required(self):
  j=self.create(); claim=self.jobs.claim_next(self.dev.token)
  self.assertTrue(claim.claim_token)
  self.assertNotIn("claim_token",claim.public_dict())
  with self.assertRaises(PermissionError):
   self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token="wrong-token-value-000000",result_text="bad")
  self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token,result_text="ok")
 def test_claim_generation_and_token_fence_stale_workers(self):
  j=self.create(); claim=self.jobs.claim_next(self.dev.token)
  with self.jobs._connect() as db:
   db.execute("UPDATE inference_jobs SET status='queued',lease_expires_at=NULL WHERE job_id=?",(j.job_id,))
  newer=self.jobs.claim_next(self.dev.token)
  self.assertEqual(newer.claim_generation,claim.claim_generation+1)
  with self.assertRaises(PermissionError):
   self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token,result_text="stale")
  with self.assertRaises(PermissionError):
   self.jobs.renew_job_lease(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token)
  self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=newer.claim_generation,claim_token=newer.claim_token,result_text="fresh")

 def test_concurrent_claims_have_one_authoritative_generation(self):
  self.create()
  with ThreadPoolExecutor(max_workers=2) as pool:
   claims=list(pool.map(lambda _: self.jobs.claim_next(self.dev.token),range(2)))
  successful=[claim for claim in claims if claim is not None]
  self.assertEqual(len(successful),1)
  self.assertEqual(successful[0].claim_generation,1)
  self.assertTrue(successful[0].claim_token)

 def test_current_claim_can_renew(self):
  j=self.create(); claim=self.jobs.claim_next(self.dev.token)
  expiry=self.jobs.renew_job_lease(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token)
  self.assertIsInstance(expiry,str)

 def test_database_connections_close_after_context_exit(self):
  connection=self.jobs._connect()
  with connection as db:
   db.execute("SELECT 1").fetchone()
  with self.assertRaises(sqlite3.ProgrammingError):
   connection.connection.execute("SELECT 1")

 def test_validation(self):
  with self.assertRaisesRegex(ValueError,"invalid_input"): self.create(input_text=" ")
  j=self.create(); claim=self.jobs.claim_next(self.dev.token)
  with self.assertRaisesRegex(ValueError,"provide_exactly"): self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token)
  with self.assertRaisesRegex(ValueError,"invalid_error_code"): self.jobs.complete_job(self.dev.token,j.job_id,claim_generation=claim.claim_generation,claim_token=claim.claim_token,error_code="shell:bad")
 def test_job_limit_and_owner_device(self):
  limited=InferenceJobService(Path(self.tmp.name)/"limited.sqlite",self.pair,max_jobs_per_owner=1)
  limited.create_job("owner-a",model_id="m",device_id=self.dev.device.device_id,input_text="1")
  with self.assertRaisesRegex(ValueError,"owner_job_limit"): limited.create_job("owner-a",model_id="m",device_id=self.dev.device.device_id,input_text="2")
  with self.assertRaises(PermissionError): self.jobs.create_job("owner-b",model_id="m",device_id=self.dev.device.device_id,input_text="bad")
if __name__=="__main__": unittest.main()
