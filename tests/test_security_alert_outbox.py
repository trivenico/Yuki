import os,tempfile,unittest
from core.security_alert_outbox import SecurityAlertOutbox

class SecurityAlertOutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.now=1000.0
        self.outbox=SecurityAlertOutbox(os.path.join(self.tmp.name,"outbox.sqlite"),authorize_operator=lambda x:x=="operator",
            clock=lambda:self.now,base_retry_seconds=5,max_retry_seconds=30,max_attempts=3,lease_seconds=10)
        self.incident={"incident_id":"a"*32,"category":"audit_integrity_failure","severity":"critical","status":"open",
            "first_seen":990.0,"last_seen":1000.0,"event_count":1,"last_reason_code":"audit_chain_invalid"}
    def tearDown(self): self.tmp.cleanup()
    def test_enqueue_idempotent_and_payload_minimized(self):
        a=self.outbox.enqueue_incident(self.incident); self.assertEqual(a,self.outbox.enqueue_incident(self.incident))
        item=self.outbox.claim("worker-1"); self.assertEqual(item["alert_id"],a)
        self.assertNotIn("secret",str(item["payload"]))
    def test_retry_backoff_and_dead_letter(self):
        self.outbox.enqueue_incident(self.incident)
        item=self.outbox.claim("worker-1"); self.assertEqual(self.outbox.complete("worker-1",item["alert_id"],success=False),"pending")
        self.assertIsNone(self.outbox.claim("worker-2"))
        self.now+=5; item=self.outbox.claim("worker-2"); self.outbox.complete("worker-2",item["alert_id"],success=False)
        self.now+=10; item=self.outbox.claim("worker-3"); self.assertEqual(self.outbox.complete("worker-3",item["alert_id"],success=False),"dead")
        self.assertEqual(self.outbox.list_alerts("operator",state="dead")[0]["attempts"],3)
    def test_delivery_and_sender_failure(self):
        self.outbox.enqueue_incident(self.incident)
        self.assertEqual(self.outbox.deliver_one("worker",lambda payload,key:True),"delivered")
        other=dict(self.incident,incident_id="b"*32)
        self.outbox.enqueue_incident(other)
        self.assertEqual(self.outbox.deliver_one("worker",lambda payload,key:(_ for _ in ()).throw(RuntimeError("secret token"))),"pending")
        self.assertEqual(self.outbox.list_alerts("operator",state="pending")[0]["last_error_code"],"provider_error")
    def test_expired_lease_can_be_reclaimed_but_old_worker_cannot_ack(self):
        self.outbox.enqueue_incident(self.incident); item=self.outbox.claim("worker-a")
        self.now+=11; claimed=self.outbox.claim("worker-b")
        self.assertEqual(claimed["alert_id"],item["alert_id"])
        with self.assertRaises(PermissionError): self.outbox.complete("worker-a",item["alert_id"],success=True)
    def test_sender_must_return_boolean_and_operator_is_authorized(self):
        self.outbox.enqueue_incident(self.incident)
        self.assertEqual(self.outbox.deliver_one("worker",lambda p,k:1),"pending")
        with self.assertRaises(PermissionError): self.outbox.list_alerts("attacker")

if __name__=="__main__": unittest.main()
