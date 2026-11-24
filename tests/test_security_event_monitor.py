import os, tempfile, unittest
from core.security_event_monitor import SecurityEventMonitor, SecuritySignal
from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.workload_identity import WorkloadSigningKey
from core.workload_key_registry import SQLiteWorkloadKeyRegistry

class SecurityEventMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.now=1000.0; self.subject="a"*64
        self.monitor=SecurityEventMonitor(os.path.join(self.tmp.name,"events.sqlite"),authorize_operator=lambda x:x=="responder",failure_threshold=3,window_seconds=60,clock=lambda:self.now)
    def tearDown(self): self.tmp.cleanup()
    def signal(self,n,category="authorization_denied",at=None,subject=None,reason="policy_denied"):
        return SecuritySignal("signal-"+str(n),category,self.now if at is None else at,reason,self.subject if subject is None else subject)
    def test_threshold_and_window(self):
        self.assertIsNone(self.monitor.record(self.signal(1))); self.assertIsNone(self.monitor.record(self.signal(2)))
        incident=self.monitor.record(self.signal(3)); self.assertEqual(incident["event_count"],3); self.assertEqual(incident["severity"],"high")
        self.assertEqual(len(self.monitor.incidents("responder")),1)
    def test_below_threshold_outside_window(self):
        self.monitor.record(self.signal(1,at=900)); self.monitor.record(self.signal(2,at=1000))
        self.assertEqual(self.monitor.incidents("responder"),())
    def test_immediate_key_and_integrity_incidents(self):
        a=self.monitor.record(self.signal(1,"key_revoked",reason="key_revoked"))
        b=self.monitor.record(self.signal(2,"audit_integrity_failure",reason="audit_chain_invalid",subject=None))
        self.assertEqual(a["severity"],"high"); self.assertEqual(b["severity"],"critical")
    def test_duplicate_signal_idempotent(self):
        s=self.signal(1,"key_revoked",reason="key_revoked")
        self.assertIsNotNone(self.monitor.record(s)); self.assertIsNone(self.monitor.record(s))
        self.assertEqual(len(self.monitor.incidents("responder")),1)
    def test_restart_and_operator_lifecycle(self):
        self.monitor.record(self.signal(1,"audit_integrity_failure",reason="audit_chain_invalid",subject=None))
        restarted=SecurityEventMonitor(self.monitor.database_path,authorize_operator=lambda x:x=="responder",clock=lambda:self.now)
        incident=restarted.incidents("responder")[0]
        with self.assertRaises(PermissionError): restarted.incidents("intruder")
        restarted.transition("responder",incident["incident_id"],"acknowledge")
        self.assertEqual(restarted.incidents("responder")[0]["status"],"acknowledged")
        restarted.transition("responder",incident["incident_id"],"resolve")
        self.assertEqual(restarted.incidents("responder")[0]["status"],"resolved")
    def test_ingests_existing_authorization_denials_idempotently(self):
        audit_path=os.path.join(self.tmp.name,"authorization.sqlite")
        audit=SQLiteAuthorizationAuditLog(audit_path)
        request=AuthorizationRequest(Principal("alice","human"),"tool:invoke","resource:x")
        audit(request,AuthorizationDecision(False,"decision-1","revision-1","policy_denied"))
        cursor=self.monitor.ingest_authorization_audit(audit)
        self.assertEqual(cursor,1)
        self.assertEqual(self.monitor.ingest_authorization_audit(audit),1)
        # One denial is below the configured threshold, but is durably imported.
        db=self.monitor._connect()
        try: self.assertEqual(db.execute("SELECT COUNT(*) FROM security_events").fetchone()[0],1)
        finally: db.close()

    def test_ingests_workload_key_revocation_audit(self):
        registry=SQLiteWorkloadKeyRegistry(os.path.join(self.tmp.name,"keys.sqlite"),
            authorize_admin=lambda x:x=="security-admin",clock=lambda:1000)
        registry.register("security-admin",WorkloadSigningKey("issuer","kid","EdDSA",b"k"*32))
        registry.activate("security-admin","issuer","kid")
        registry.revoke("security-admin","issuer","kid")
        cursor=self.monitor.ingest_key_lifecycle(registry,"security-admin")
        self.assertEqual(cursor,3)
        incidents=self.monitor.incidents("responder")
        self.assertEqual(len(incidents),1)
        self.assertEqual(incidents[0]["category"],"key_revoked")

    def test_rejects_raw_subject_and_sensitive_reason(self):
        with self.assertRaises(ValueError): self.monitor.record(SecuritySignal("bad","authorization_denied",self.now,"denied","raw-user"))
        with self.assertRaises(ValueError): self.monitor.record(SecuritySignal("bad2","authorization_denied",self.now,"token=secret",self.subject))

if __name__=="__main__": unittest.main()
