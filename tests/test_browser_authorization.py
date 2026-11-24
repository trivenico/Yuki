import unittest

from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway
from automation.browser import BrowserAutomationError, SafeBrowser


class _Policy:
    def __init__(self, allowed=True, revision="policy-v1"):
        self.allowed = allowed
        self.revision = revision
        self.requests = []

    def authorize(self, request):
        self.requests.append(request)
        return AuthorizationDecision(
            self.allowed, "decision-1", self.revision,
            "allowed" if self.allowed else "denied",
        )


class SafeBrowserAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.policy = _Policy()
        self.audit = []
        self.gateway = AuthorizationGateway(
            self.policy, audit=lambda request, decision: self.audit.append((request, decision)),
        )
        self.principal = Principal("alice", "human", tenant_id="tenant-1")

    def test_navigation_is_bound_to_authenticated_principal_and_resource(self):
        browser = SafeBrowser(
            allowed_hosts=("docs.example.com",),
            authorization_gateway=self.gateway,
            principal=self.principal,
            policy_revision="policy-v1",
            task_id="task-7",
            delegation_id="grant-2",
            approval_id="approval-3",
        )
        # Isolate authorization construction from DNS and browser dependencies.
        browser._validate = lambda url: url
        self.assertEqual(
            browser._authorize_navigation("https://docs.example.com/guides/start?token=secret"),
            "https://docs.example.com/guides/start?token=secret",
        )
        request = self.policy.requests[0]
        self.assertIs(request.principal, self.principal)
        self.assertEqual(request.action, "browser:navigate")
        self.assertTrue(request.resource.startswith("browser:sha256:"))
        self.assertEqual(len(request.resource), len("browser:sha256:") + 64)
        self.assertEqual(request.task_id, "task-7")
        self.assertEqual(request.delegation_id, "grant-2")
        self.assertEqual(request.approval_id, "approval-3")
        self.assertEqual(request.expected_policy_revision, "policy-v1")
        self.assertEqual(len(self.audit), 1)
        self.assertNotIn("secret", request.resource)

    def test_denial_prevents_browser_startup(self):
        policy = _Policy(allowed=False)
        gateway = AuthorizationGateway(policy, audit=lambda *_: None)
        browser = SafeBrowser(
            allowed_hosts=("docs.example.com",),
            authorization_gateway=gateway,
            principal=self.principal,
            policy_revision="policy-v1",
        )
        browser._validate = lambda url: url
        with self.assertRaisesRegex(BrowserAutomationError, "central security policy"):
            browser.read_page("https://docs.example.com/private")
        self.assertEqual(len(policy.requests), 1)

    def test_gateway_audit_failure_prevents_navigation(self):
        gateway = AuthorizationGateway(self.policy, audit=lambda *_: False)
        browser = SafeBrowser(
            allowed_hosts=("docs.example.com",),
            authorization_gateway=gateway,
            principal=self.principal,
            policy_revision="policy-v1",
        )
        browser._validate = lambda url: url
        with self.assertRaises(BrowserAutomationError):
            browser._authorize_navigation("https://docs.example.com/")
        self.assertEqual(len(self.policy.requests), 1)

    def test_gateway_and_principal_must_be_configured_together(self):
        with self.assertRaises(ValueError):
            SafeBrowser(allowed_hosts=("docs.example.com",), authorization_gateway=self.gateway)


if __name__ == "__main__":
    unittest.main()
