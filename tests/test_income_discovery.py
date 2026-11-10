import unittest
from datetime import date, datetime, timezone

from core.income_discovery import (
    DiscoveryError,
    IncomeOpportunityDiscoveryService,
    OpportunityCandidate,
)
from core.platform_registry import PlatformAction, PlatformCapability, PlatformRegistry


class _FakeAdapter:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def discover(self, *, query, region, limit):
        self.calls.append((query, region, limit))
        if self.error:
            raise self.error
        return self.results


def _platform(**overrides):
    values = {
        "platform_id": "sample-market",
        "display_name": "Sample Market",
        "canonical_domains": ("market.example",),
        "categories": ("freelance",),
        "supported_regions": ("GB",),
        "actions": (
            PlatformAction(
                "read_opportunities",
                automation_mode="official_api",
                required_scopes=("opportunities.read",),
                owner_approval_required=False,
            ),
        ),
        "policy_url": "https://market.example/terms",
        "policy_reviewed_on": date(2026, 1, 1),
        "policy_recheck_by": date(2027, 1, 1),
        "adapter_version": "1.2.0",
        "enabled": True,
    }
    values.update(overrides)
    return PlatformCapability(**values)


def _candidate(**overrides):
    values = {
        "external_id": "job-123",
        "title": "Backend engineering contract",
        "category": "freelance",
        "source_url": "https://market.example/jobs/123",
        "summary": "Build a service integration.",
        "observed_at": datetime(2026, 11, 10, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return OpportunityCandidate(**values)


class IncomeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.adapter = _FakeAdapter([_candidate()])
        self.registry = PlatformRegistry([_platform()])
        self.service = IncomeOpportunityDiscoveryService(
            self.registry, {"sample-market": self.adapter}
        )

    def test_current_official_read_adapter_returns_source_attributed_batch(self):
        batch = self.service.discover(
            "sample-market", query="python", region="GB", limit=10,
            on_date=date(2026, 11, 10),
        )
        self.assertEqual(batch.platform_id, "sample-market")
        self.assertEqual(batch.adapter_version, "1.2.0")
        self.assertEqual(batch.region, "GB")
        self.assertEqual(batch.candidates, (_candidate(),))
        self.assertEqual(self.adapter.calls, [("python", "GB", 10)])
        self.assertIsNotNone(batch.retrieved_at.tzinfo)

    def test_unknown_disabled_stale_or_unreviewed_platform_fails_closed(self):
        cases = [
            (PlatformRegistry(), "platform_not_available"),
            (PlatformRegistry([_platform(enabled=False)]), "platform_read_access_not_approved"),
            (PlatformRegistry([_platform(policy_recheck_by=date(2026, 1, 2))]), "platform_read_access_not_approved"),
            (PlatformRegistry([_platform(policy_url=None, policy_reviewed_on=None, policy_recheck_by=None)]), "platform_read_access_not_approved"),
        ]
        for registry, expected in cases:
            with self.subTest(expected=expected):
                service = IncomeOpportunityDiscoveryService(registry, {"sample-market": self.adapter})
                with self.assertRaisesRegex(DiscoveryError, expected):
                    service.discover("sample-market", region="GB", on_date=date(2026, 11, 10))

    def test_requires_official_api_mode_and_installed_adapter(self):
        user_present = PlatformRegistry([_platform(actions=(
            PlatformAction("read_opportunities", automation_mode="user_present_browser", owner_approval_required=False),
        ))])
        with self.assertRaisesRegex(DiscoveryError, "not_approved"):
            IncomeOpportunityDiscoveryService(user_present, {"sample-market": self.adapter}).discover(
                "sample-market", region="GB", on_date=date(2026, 11, 10)
            )
        with self.assertRaisesRegex(DiscoveryError, "adapter_not_installed"):
            IncomeOpportunityDiscoveryService(self.registry, {}).discover(
                "sample-market", region="GB", on_date=date(2026, 11, 10)
            )

    def test_region_query_and_limit_are_bounded(self):
        for kwargs, expected in [
            ({"region": "US"}, "region_not_supported"),
            ({"region": "gb"}, "invalid_discovery_region"),
            ({"region": "GB", "limit": 51}, "invalid_discovery_limit"),
            ({"region": "GB", "query": "x" * 301}, "invalid_discovery_query"),
        ]:
            with self.subTest(expected=expected), self.assertRaisesRegex(DiscoveryError, expected):
                self.service.discover("sample-market", on_date=date(2026, 11, 10), **kwargs)

    def test_candidate_domain_scheme_credentials_and_port_are_restricted(self):
        for url in (
            "http://market.example/jobs/1",
            "https://evil.example/jobs/1",
            "https://market.example.evil.example/jobs/1",
            "https://user:pass@market.example/jobs/1",
            "https://market.example:444/jobs/1",
        ):
            with self.subTest(url=url), self.assertRaises(DiscoveryError):
                self.service._validate_candidate(_candidate(source_url=url), ("market.example",))

    def test_invalid_candidate_metadata_and_timezone_are_rejected(self):
        with self.assertRaisesRegex(DiscoveryError, "candidate_timestamp"):
            self.service._validate_candidate(_candidate(observed_at=datetime(2026, 11, 10)), ("market.example",))
        with self.assertRaises(DiscoveryError):
            self.service._validate_candidate(_candidate(title=" " * 2), ("market.example",))

    def test_adapter_exceptions_are_sanitized_and_duplicates_are_deduplicated(self):
        failing = _FakeAdapter(error=RuntimeError("token=secret-value"))
        service = IncomeOpportunityDiscoveryService(self.registry, {"sample-market": failing})
        with self.assertRaisesRegex(DiscoveryError, "platform_discovery_failed") as raised:
            service.discover("sample-market", region="GB", on_date=date(2026, 11, 10))
        self.assertNotIn("secret-value", str(raised.exception))

        duplicate = _FakeAdapter([_candidate(), _candidate(title="Duplicate")])
        batch = IncomeOpportunityDiscoveryService(
            self.registry, {"sample-market": duplicate}
        ).discover("sample-market", region="GB", on_date=date(2026, 11, 10))
        self.assertEqual(len(batch.candidates), 1)

    def test_adapter_cannot_return_more_than_requested_or_unknown_types(self):
        for results in ([_candidate(), _candidate(external_id="job-456")], [object()]):
            adapter = _FakeAdapter(results)
            service = IncomeOpportunityDiscoveryService(self.registry, {"sample-market": adapter})
            with self.assertRaises(DiscoveryError):
                service.discover("sample-market", region="GB", limit=1, on_date=date(2026, 11, 10))


if __name__ == "__main__":
    unittest.main()
