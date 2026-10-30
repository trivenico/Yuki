import tempfile
import unittest
from pathlib import Path
from learning.dataset_registry import DatasetApproval, DatasetRegistry
from learning.training_jobs import TrainingJobError, TrainingJobQueue

class TrainingQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.registry=DatasetRegistry()
        self.approval=DatasetApproval(repo_id="openai/gsm8k",revision="a"*40,license_id="MIT",
          license_evidence_url="https://huggingface.co/datasets/openai/gsm8k",
          provenance_reviewed=True,privacy_reviewed=True,security_reviewed=True,
          human_approved=True,approved_by="reviewer")
        self.registry.approve(self.approval)
        self.path=Path(self.tmp.name)/"jobs.sqlite"
        self.queue=TrainingJobQueue(self.path,dataset_registry=self.registry,max_ram_gb=16,max_vram_gb=12)
    def create(self):
        return self.queue.create(approval=self.approval,base_model="org/model",method="qlora",
          requested_ram_gb=8,requested_vram_gb=6,max_runtime_minutes=90)
    def test_approval_required(self):
        q=TrainingJobQueue(self.path,dataset_registry=DatasetRegistry())
        with self.assertRaises(PermissionError):
            q.create(approval=self.approval,base_model="m",method="lora",requested_ram_gb=4)
    def test_limits(self):
        with self.assertRaises(ValueError):
            self.queue.create(approval=self.approval,base_model="m",method="full",requested_ram_gb=4)
        with self.assertRaises(ValueError):
            self.queue.create(approval=self.approval,base_model="m",method="lora",requested_ram_gb=32)
    def test_pause_resume_cancel(self):
        j=self.create()
        self.assertEqual(self.queue.pause(j.job_id).state,"paused")
        self.assertEqual(self.queue.resume(j.job_id).state,"queued")
        self.assertEqual(self.queue.cancel(j.job_id).state,"cancelled")
    def test_idle_power_and_capacity_gate(self):
        j=self.create()
        args=dict(worker_id="w",available_ram_gb=16,available_vram_gb=12)
        self.assertIsNone(self.queue.claim_next(**args,machine_idle=False,on_external_power=True))
        self.assertIsNone(self.queue.claim_next(**args,machine_idle=True,on_external_power=False))
        self.assertIsNone(self.queue.claim_next(**{**args,"available_ram_gb":4},machine_idle=True,on_external_power=True))
        self.assertEqual(self.queue.claim_next(**args,machine_idle=True,on_external_power=True).job_id,j.job_id)
    def test_finish_requires_lease_owner(self):
        j=self.create()
        self.queue.claim_next(worker_id="owner",available_ram_gb=16,available_vram_gb=12,machine_idle=True,on_external_power=True)
        with self.assertRaises(TrainingJobError): self.queue.finish(j.job_id,worker_id="other",success=True)
        self.assertEqual(self.queue.finish(j.job_id,worker_id="owner",success=True).state,"succeeded")
    def test_persistence(self):
        j=self.create()
        reopened=TrainingJobQueue(self.path,dataset_registry=self.registry)
        self.assertEqual(reopened.get(j.job_id).dataset_revision,"a"*40)

if __name__=="__main__": unittest.main()
