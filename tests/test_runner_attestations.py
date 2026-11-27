import hashlib, json, secrets, tempfile, unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from evaluation.evolution_candidates import EvolutionCandidate
from evaluation.execution_receipts import create_execution_manifest
from evaluation.runner_attestations import (RunnerAttestation, RunnerAttestationReplayStore,
    RunnerVerificationKey, verify_runner_attestation)

class RunnerAttestationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.now=datetime(2026,11,27,8,0,tzinfo=timezone.utc)
        self.private=Ed25519PrivateKey.generate()
        public=self.private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.key=RunnerVerificationKey("owner-a","key-1",public,"2026-11-27T00:00:00+00:00")
        candidate=EvolutionCandidate(candidate_id="candidate-a",owner_id="owner-a",kind="prompt",
            title="Title",rationale="Reason",expected_outcome="Outcome",evidence_refs=(),
            risk_tags=(),parent_digest=None,payload_digest="a"*64,
            created_at="2026-11-27T00:00:00+00:00",state="accepted_for_evaluation",revision=1)
        self.manifest=create_execution_manifest(candidate=candidate,artifact=b"artifact",
            benchmark_id="bench",benchmark_version=1,benchmark_digest="b"*64,
            input_manifest=b"{}",sandbox_image="image",runtime_identity="runtime",
            runner_version="1",attempt_id="attempt",created_at="2026-11-27T07:59:00+00:00")
        self.store=RunnerAttestationReplayStore(Path(self.temp.name)/"nonce.sqlite")
    def signed(self,**changes):
        fields=dict(schema="yuki.runner-attestation.v1",owner_id="owner-a",
            runner_id="runner-1",key_id="key-1",nonce=secrets.token_urlsafe(24),
            issued_at="2026-11-27T08:00:00+00:00",manifest_digest=self.manifest.manifest_digest,
            loaded_artifact_digest=self.manifest.artifact_digest,exit_code=0,timed_out=False,
            output_digest=hashlib.sha256(b"ok").hexdigest(),signature=b"")
        fields.update(changes); unsigned=RunnerAttestation(**fields)
        signature=self.private.sign(json.dumps(unsigned.payload(),sort_keys=True,
            separators=(",",":"),ensure_ascii=True,allow_nan=False).encode())
        return replace(unsigned,signature=signature)
    def verify(self,item,key=None):
        return verify_runner_attestation(attestation=item,manifest=self.manifest,
            key=key or self.key,replay_store=self.store,now=self.now)
    def test_valid_signature(self):
        self.assertEqual(self.verify(self.signed()).loaded_artifact_digest,self.manifest.artifact_digest)
    def test_replay(self):
        item=self.signed(nonce="A"*32); self.verify(item)
        with self.assertRaisesRegex(ValueError,"already been used"): self.verify(item)
    def test_signature_tampering(self):
        with self.assertRaisesRegex(ValueError,"signature is invalid"):
            self.verify(replace(self.signed(),runner_id="forged"))
    def test_loaded_artifact_mismatch(self):
        with self.assertRaisesRegex(ValueError,"exact artifact digest"):
            self.verify(self.signed(loaded_artifact_digest="d"*64))
    def test_revoked_key(self):
        key=replace(self.key,revoked_at="2026-11-27T07:59:30+00:00")
        with self.assertRaisesRegex(ValueError,"revoked"): self.verify(self.signed(),key)
    def test_rotated_key_id(self):
        with self.assertRaisesRegex(ValueError,"key ID mismatch"):
            self.verify(self.signed(),replace(self.key,key_id="key-2"))
    def test_stale_and_future(self):
        with self.assertRaisesRegex(ValueError,"stale"):
            self.verify(self.signed(issued_at="2026-11-27T07:50:00+00:00"))
        with self.assertRaisesRegex(ValueError,"future"):
            self.verify(self.signed(issued_at="2026-11-27T08:05:00+00:00"))
    def test_owner_mismatch(self):
        with self.assertRaisesRegex(ValueError,"owner mismatch"):
            self.verify(self.signed(owner_id="other"))

if __name__=="__main__": unittest.main()
