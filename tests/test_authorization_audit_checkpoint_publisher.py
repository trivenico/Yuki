import unittest

from core.authorization_audit import (
    AuthorizationAuditCheckpoint,
    AuthorizationAuditCheckpointPublisher,
)


class FakeAuditLog:
    def __init__(self, events, checkpoint):
        self.events = tuple(events)
        self.current = checkpoint

    def __call__(self, request, decision):
        raise NotImplementedError

    def verify_chain(self):
        return True

    def head_hash(self):
        return self.current.event_hash

    def checkpoint(self):
        return self.current

    def verify_anchor(self, anchor, *, max_age_seconds=None, now=None):
        latest = anchor.latest()
        return latest is not None and latest.event_id <= self.current.event_id

    def publish_checkpoint(self, anchor):
        raise NotImplementedError

    def event_page(self, *, after_event_id=0, limit=500):
        return tuple(event for event in self.events if event["event_id"] > after_event_id)[:limit]

    def evidence_page(self, *, after_event_id=0, limit=500):
        return tuple(event for event in self.events if event["event_id"] > after_event_id)[:limit]

    def close(self):
        pass


class FakeAnchor:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.publications = []

    def latest(self):
        return self.checkpoint

    def publish(self, checkpoint):
        self.publications.append(checkpoint)
        self.checkpoint = checkpoint


class FakeEvidenceVerifier:
    def __init__(self, accepted):
        self.accepted = accepted
        self.calls = []

    def verify_batch(self, previous, current, events):
        self.calls.append((previous, current, events))
        return self.accepted


def event(event_id):
    previous_hash = ("a" if event_id == 2 else "b") * 64
    value = {
        "event_id": event_id,
        "occurred_at": "2026-11-25T10:00:00+00:00",
        "principal_hash": "a" * 64,
        "tenant_hash": None,
        "agent_hash": None,
        "action": "task:read",
        "resource_hash": "b" * 64,
        "task_hash": None,
        "delegation_hash": None,
        "approval_hash": None,
        "risk": "low",
        "allowed": 1,
        "decision_hash": "d" * 64,
        "policy_revision_hash": "e" * 64,
        "reason_code": "policy_allow",
        "previous_hash": previous_hash,
    }
    import hashlib
    import json
    value["event_hash"] = hashlib.sha256(json.dumps(
        {key: item for key, item in value.items() if key != "event_id"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return value


class AuthorizationAuditCheckpointPublisherTests(unittest.TestCase):
    def setUp(self):
        self.previous = AuthorizationAuditCheckpoint(1, "a" * 64)
        self.current = AuthorizationAuditCheckpoint(3, "c" * 64)
        self.anchor = FakeAnchor(self.previous)
        self.audit = FakeAuditLog((event(2), event(3)), self.current)

    def test_rejects_new_checkpoint_without_independent_evidence(self):
        verifier = FakeEvidenceVerifier(False)
        publisher = AuthorizationAuditCheckpointPublisher(
            self.audit, self.anchor, verifier
        )
        with self.assertRaisesRegex(RuntimeError, "independent_evidence_rejected"):
            publisher.run_once()
        self.assertEqual(self.anchor.publications, [])
        self.assertEqual(len(verifier.calls), 1)

    def test_publishes_only_after_independent_evidence_accepts_complete_batch(self):
        verifier = FakeEvidenceVerifier(True)
        publisher = AuthorizationAuditCheckpointPublisher(
            self.audit, self.anchor, verifier
        )
        published = publisher.run_once()
        self.assertEqual(published.event_id, 3)
        self.assertEqual(len(self.anchor.publications), 1)
        self.assertEqual(
            [item["event_id"] for item in verifier.calls[0][2]], [2, 3]
        )

    def test_rejects_event_sequence_gap_before_consulting_verifier(self):
        self.audit.events = (event(3),)
        verifier = FakeEvidenceVerifier(True)
        publisher = AuthorizationAuditCheckpointPublisher(
            self.audit, self.anchor, verifier
        )
        with self.assertRaisesRegex(RuntimeError, "evidence_batch_invalid"):
            publisher.run_once()
        self.assertEqual(verifier.calls, [])
        self.assertEqual(self.anchor.publications, [])

    def test_rejects_batch_whose_final_hash_does_not_match_checkpoint(self):
        self.audit.events = (event(2), {
            **event(3), "event_hash": "d" * 64,
        })
        verifier = FakeEvidenceVerifier(True)
        publisher = AuthorizationAuditCheckpointPublisher(
            self.audit, self.anchor, verifier
        )
        with self.assertRaisesRegex(RuntimeError, "evidence_checkpoint_mismatch"):
            publisher.run_once()
        self.assertEqual(verifier.calls, [])
        self.assertEqual(self.anchor.publications, [])

    def test_rejects_evidence_row_changed_after_chain_verification(self):
        changed = {**event(2), "action": "task:admin"}
        verifier = FakeEvidenceVerifier(True)
        self.audit.events = (changed, event(3))
        publisher = AuthorizationAuditCheckpointPublisher(
            self.audit, self.anchor, verifier
        )
        with self.assertRaisesRegex(RuntimeError, "event_hash_mismatch"):
            publisher.run_once()
        self.assertEqual(verifier.calls, [])
        self.assertEqual(self.anchor.publications, [])

    def test_does_not_require_new_evidence_when_checkpoint_is_unchanged(self):
        self.audit.current = self.previous
        verifier = FakeEvidenceVerifier(False)
        publisher = AuthorizationAuditCheckpointPublisher(
            self.audit, self.anchor, verifier
        )
        published = publisher.run_once()
        self.assertEqual(published.event_id, self.previous.event_id)
        self.assertEqual(verifier.calls, [])


if __name__ == "__main__":
    unittest.main()
