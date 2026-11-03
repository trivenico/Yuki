
from core.inference_jobs import InferenceJobService

class InferenceJobAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.owner = self.auth.bootstrap_admin("job-owner", "a-secure-owner-password")
        self.session = self.auth.authenticate("job-owner", "a-secure-owner-password")
        self.pairing = DevicePairingService(root / "pair.sqlite")
        challenge = self.pairing.create_pairing_challenge(self.owner.user_id)
        self.device = self.pairing.pair_device(challenge.code, "Inference worker")
        self.jobs = InferenceJobService(root / "jobs.sqlite", self.pairing)
        def model(**kwargs): return {"text": "ok", "tool_calls": {"tool_calls": []}}
        loop = AgentExecutionLoop(model, StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals())))
        service = ConversationService(ConversationStore(root / "conversations.sqlite"), loop)
        self.app = YukiConversationAPI(self.auth, service, inference_jobs=self.jobs)

    def tearDown(self): self.temp.cleanup()

    def request(self, method, path, payload=None, token=None):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            sent, messages = False, []
            async def receive():
                nonlocal sent
                if sent: return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message): messages.append(message)
            headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
            await self.app({"type":"http","method":method,"path":path,"headers":headers,"query_string":b""},receive,send)
            return messages[0]["status"], json.loads(messages[1]["body"])
        return asyncio.run(run())

    def test_owner_create_worker_claim_complete_and_read(self):
        status, data = self.request("POST", "/v1/inference/jobs", {
            "model_id":"local/model", "device_id":self.device.device.device_id, "input_text":"Hello"
        }, self.session.token)
        self.assertEqual(status, 201)
        job_id=data["job"]["job_id"]
        self.assertNotIn("input_text", data["job"])
        status, claimed=self.request("POST","/v1/inference/claim",token=self.device.token)
        self.assertEqual(status,200)
        self.assertEqual(claimed["job"]["job_id"],job_id)
        status, completed=self.request("POST",f"/v1/inference/jobs/{job_id}/complete",{"result_text":"Hello back"},self.device.token)
        self.assertEqual(status,200)
        status, owner_view=self.request("GET",f"/v1/inference/jobs/{job_id}",token=self.session.token)
        self.assertEqual(status,200)
        self.assertEqual(owner_view["job"]["status"],"succeeded")
        self.assertEqual(owner_view["job"]["result_text"],"Hello back")

    def test_worker_credential_cannot_create_owner_job(self):
        status, data=self.request("POST","/v1/inference/jobs",{
            "model_id":"local/model","device_id":self.device.device.device_id,"input_text":"x"
        },self.device.token)
        self.assertEqual(status,401)

    def test_user_session_cannot_claim_worker_job(self):
        status, data=self.request("POST","/v1/inference/claim",token=self.session.token)
        self.assertEqual(status,401)

    def test_foreign_device_cannot_complete_claimed_job(self):
        job=self.jobs.create_job(self.owner.user_id,model_id="local/model",
            device_id=self.device.device.device_id,input_text="hello")
        self.jobs.claim_next(self.device.token)
        other_challenge=self.pairing.create_pairing_challenge("another-owner")
        other=self.pairing.pair_device(other_challenge.code,"Foreign")
        status,data=self.request("POST",f"/v1/inference/jobs/{job.job_id}/complete",
            {"result_text":"forged"},other.token)
        self.assertEqual(status,404)

    def test_unconfigured_routes_fail_closed(self):
        self.app.inference_jobs=None
        status,data=self.request("POST","/v1/inference/claim",token=self.device.token)
        self.assertEqual(status,503)

