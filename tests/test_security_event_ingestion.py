import os,tempfile,unittest
from core.security_event_monitor import SecurityEventMonitor,SecuritySignal
from core.security_event_ingestion import SecurityEventIngestionCursors,DurableSecurityEventIngestor
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.authorization import AuthorizationDecision,AuthorizationRequest,Principal

class DurableSecurityEventIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.now=1000.0
        self.monitor=SecurityEventMonitor(os.path.join(self.tmp.name,"monitor.sqlite"),authorize_operator=lambda x:x=="ops",clock=lambda:self.now)
        self.cursors=SecurityEventIngestionCursors(os.path.join(self.tmp.name,"cursor.sqlite"),clock=lambda:self.now,lease_seconds=10)
        self.audit=SQLiteAuthorizationAuditLog(os.path.join(self.tmp.name,"audit.sqlite"))
        self.ingestor=DurableSecurityEventIngestor(self.monitor,self.cursors,batch_size=2)
    def tearDown(self): self.tmp.cleanup()
    def add_denial(self,n):
        self.audit(AuthorizationRequest(Principal("user"+str(n),"human"),"tool:invoke","resource:x"),
            AuthorizationDecision(False,"decision-"+str(n),"revision-1","policy_denied"))
    def test_cursor_advances_only_after_success_and_replay_is_safe(self):
        self.add_denial(1); self.add_denial(2); self.add_denial(3)
        first=self.ingestor.ingest_authorization_page(self.audit,"worker")
        self.assertEqual(first["cursor"],2)
        restarted=DurableSecurityEventIngestor(self.monitor,self.cursors,batch_size=2)
        second=restarted.ingest_authorization_page(self.audit,"worker")
        self.assertEqual(second["cursor"],3)
        db=self.monitor._connect()
        try: self.assertEqual(db.execute("SELECT COUNT(*) FROM security_events").fetchone()[0],3)
        finally: db.close()
    def test_concurrent_claim_is_exclusive_until_lease_expires(self):
        claim=self.cursors.claim("authorization_audit","one")
        self.assertIsNotNone(claim)
        self.assertIsNone(self.cursors.claim("authorization_audit","two"))
        self.now+=11
        replacement=self.cursors.claim("authorization_audit","two")
        with self.assertRaises(PermissionError): self.cursors.complete(claim,"one",0)
        self.assertEqual(self.cursors.complete(replacement,"two",0),0)
    def test_expired_worker_cannot_fail_or_clear_ingestion_lease(self):
        stale=self.cursors.claim("authorization_audit","worker-a")
        self.now+=11
        with self.assertRaises(PermissionError):
            self.cursors.fail(stale,"worker-a","late_failure")
        replacement=self.cursors.claim("authorization_audit","worker-b")
        self.assertIsNotNone(replacement)
        with self.assertRaises(PermissionError):
            self.cursors.fail(stale,"worker-a","late_failure")
        self.assertEqual(self.cursors.complete(replacement,"worker-b",0),0)
    def test_failed_import_does_not_advance_cursor(self):
        class Broken:
            database_path=self.audit.database_path
        with self.assertRaises(Exception): self.ingestor.ingest_authorization_page(Broken(),"worker")
        self.assertEqual(self.cursors.snapshot()["authorization_audit"]["event_id"],0)
    def test_batch_size_is_bounded(self):
        with self.assertRaises(ValueError):
            DurableSecurityEventIngestor(self.monitor,self.cursors,batch_size=5001)
    def test_failure_status_persists_without_exception_text(self):
        claim=self.cursors.claim("authorization_audit","worker")
        self.cursors.fail(claim,"worker","source_unavailable")
        state=self.cursors.snapshot()["authorization_audit"]
        self.assertEqual(state["last_error_code"],"source_unavailable")
        self.assertEqual(state["event_id"],0)

if __name__=="__main__": unittest.main()
