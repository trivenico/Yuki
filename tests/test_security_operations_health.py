import os,tempfile,unittest
from core.security_event_monitor import SecurityEventMonitor,SecuritySignal
from core.security_alert_outbox import SecurityAlertOutbox
from core.security_operations_health import SecurityWorkerHeartbeat,SecurityOperationsHealth

class SecurityOperationsHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.now=1000.0
        self.monitor=SecurityEventMonitor(os.path.join(self.tmp.name,"m.sqlite"),authorize_operator=lambda x:x=="ops",clock=lambda:self.now)
        self.outbox=SecurityAlertOutbox(os.path.join(self.tmp.name,"o.sqlite"),authorize_operator=lambda x:x=="ops",clock=lambda:self.now)
        self.beats=SecurityWorkerHeartbeat(os.path.join(self.tmp.name,"h.sqlite"),clock=lambda:self.now)
        self.health=SecurityOperationsHealth(self.monitor,self.outbox,self.beats,authorize_operator=lambda x:x=="ops",clock=lambda:self.now)
    def tearDown(self): self.tmp.cleanup()
    def test_healthy_pipeline_requires_fresh_ingestion_and_all_workers(self):
        self.monitor.record(SecuritySignal("s","key_revoked",self.now,"key_revoked"))
        for role in self.beats.ROLES: self.beats.beat(role,"worker-1")
        result=self.health.snapshot("ops")
        self.assertEqual(result["status"],"healthy")
        self.assertEqual(result["monitor"]["event_count"],1)
    def test_missing_or_stale_worker_marks_unhealthy(self):
        self.monitor.record(SecuritySignal("s","key_revoked",self.now,"key_revoked"))
        for role in self.beats.ROLES: self.beats.beat(role,"worker-1")
        self.now+=121
        self.assertFalse(self.health.snapshot("ops")["checks"]["worker_heartbeats"])
    def test_dead_letter_and_backlog_are_visible_without_payload(self):
        incident={"incident_id":"a"*32,"category":"audit_integrity_failure","severity":"critical","status":"open",
            "first_seen":990.0,"last_seen":1000.0,"event_count":1,"last_reason_code":"audit_chain_invalid"}
        self.outbox.enqueue_incident(incident)
        result=self.health.snapshot("ops")
        self.assertEqual(result["outbox"]["pending"],1)
        self.assertNotIn("payload",str(result))
        self.assertTrue(result["checks"]["alert_backlog"])
    def test_operator_authorization_fails_closed(self):
        with self.assertRaises(PermissionError): self.health.snapshot("attacker")
    def test_threshold_validation(self):
        with self.assertRaises(ValueError):
            SecurityOperationsHealth(self.monitor,self.outbox,self.beats,authorize_operator=lambda x:True,max_pending_alerts=0)
    def test_worker_role_validation(self):
        with self.assertRaises(ValueError): self.beats.beat("unknown","worker")

if __name__=="__main__": unittest.main()
