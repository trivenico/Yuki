import fcntl, os, unittest
from evaluation.evolution_candidates import EvolutionCandidate
from evaluation.execution_receipts import create_execution_manifest
from evaluation.runner_artifact_staging import stage_sealed_artifact

@unittest.skipUnless(hasattr(os,"memfd_create") and hasattr(fcntl,"F_ADD_SEALS"),
                     "Linux memfd sealing is required")
class SealedRunnerArtifactTests(unittest.TestCase):
    def setUp(self):
        self.artifact=b"verified candidate artifact"
        candidate=EvolutionCandidate(candidate_id="candidate-a",owner_id="owner-a",
            kind="prompt",title="Title",rationale="Reason",expected_outcome="Outcome",
            evidence_refs=(),risk_tags=(),parent_digest=None,payload_digest="a"*64,
            created_at="2026-11-27T00:00:00+00:00",state="accepted_for_evaluation",revision=1)
        self.manifest=create_execution_manifest(candidate=candidate,artifact=self.artifact,
            benchmark_id="bench",benchmark_version=1,benchmark_digest="b"*64,
            input_manifest=b"{}",sandbox_image="image",runtime_identity="runner",
            runner_version="1",attempt_id="attempt-a",created_at="2026-11-27T00:00:00+00:00")
    def test_exact_bytes_and_seals(self):
        with stage_sealed_artifact(manifest=self.manifest,artifact=self.artifact) as staged:
            self.assertEqual(staged.artifact_digest,self.manifest.artifact_digest)
            self.assertEqual(os.read(staged.fileno(),len(self.artifact)),self.artifact)
            seals=fcntl.fcntl(staged.fileno(),fcntl.F_GET_SEALS)
            required=(fcntl.F_SEAL_SEAL|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_WRITE)
            self.assertEqual(seals & required,required)
            with self.assertRaises(OSError): os.write(staged.fileno(),b"mutation")

    def test_subprocess_duplicate_preserves_seals_and_is_caller_owned(self):
        with stage_sealed_artifact(manifest=self.manifest,artifact=self.artifact) as staged:
            duplicate=staged.duplicate_for_subprocess()
            try:
                self.assertFalse(os.get_inheritable(duplicate))
                os.lseek(duplicate,0,os.SEEK_SET)
                self.assertEqual(os.read(duplicate,len(self.artifact)),self.artifact)
                seals=fcntl.fcntl(duplicate,fcntl.F_GET_SEALS)
                self.assertTrue(seals & fcntl.F_SEAL_WRITE)
            finally:
                os.close(duplicate)
\n    def test_digest_mismatch(self):
        with self.assertRaisesRegex(ValueError,"do not match"):
            stage_sealed_artifact(manifest=self.manifest,artifact=b"altered")
    def test_closed_descriptor(self):
        staged=stage_sealed_artifact(manifest=self.manifest,artifact=self.artifact)
        staged.close()
        with self.assertRaisesRegex(ValueError,"closed"): staged.fileno()

if __name__=="__main__": unittest.main()
