import unittest

from spatial.source_registry import (
    Freshness, LicensePolicy, Permission, Sensitivity, SpatialRecord,
    SpatialSource, SpatialSourceRegistry, validate_source,
)


class SpatialSourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.source = SpatialSource(
            "public-weather", "Example Weather", "1", "weather", "1",
            LicensePolicy("CC-BY-4.0", "Attribution required",
                          Permission.ALLOWED, Permission.ALLOWED, 30),
            endpoint_origin="https://api.example.org", enabled=True,
        )
        self.registry = SpatialSourceRegistry([self.source])

    def test_registry_rejects_duplicates_and_filters_disabled_sources(self):
        self.assertEqual(self.registry.list_sources(enabled_only=True), (self.source,))
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register(self.source)

    def test_unknown_license_is_not_assumed_to_allow_use(self):
        policy = LicensePolicy("unknown")
        source = SpatialSource("local-feed", "Local", "1", "devices", "1", policy)
        self.assertEqual(validate_source(source), [])
        self.assertEqual(policy.commercial_use, Permission.UNKNOWN)

    def test_rejects_untrusted_endpoint_origins(self):
        for origin in (
            "http://api.example.org", "https://user:pass@example.org",
            "https://example.org/path", "https://127.0.0.1", "https://localhost",
            "https://router.local", "https://example.org?token=x",
        ):
            with self.subTest(origin=origin):
                source = SpatialSource(
                    "bad-source", "Provider", "1", "weather", "1",
                    LicensePolicy("unknown"), endpoint_origin=origin,
                )
                self.assertTrue(any("endpoint_origin" in error or "local hostname" in error
                                    or "non-public IP" in error for error in validate_source(source)))

    def test_source_budgets_are_bounded(self):
        source = SpatialSource(
            "unbounded", "Provider", "1", "weather", "1", LicensePolicy("unknown"),
            max_response_bytes=100_000_000,
        )
        self.assertTrue(any("max_response_bytes" in error for error in validate_source(source)))

    def test_record_requires_registered_source_and_stable_id(self):
        missing = SpatialRecord("missing", "r1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
        self.assertIn("record references an unregistered source", missing.validate(self.registry))
        bad = SpatialRecord("public-weather", "bad id", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
        self.assertTrue(any("record_id" in error for error in bad.validate(self.registry)))

    def test_timestamps_are_timezone_aware_and_ordered(self):
        naive = SpatialRecord("public-weather", "r1", "2026-01-01T00:00:00", "2026-01-01T00:01:00Z")
        self.assertTrue(any("timezone" in error for error in naive.validate(self.registry)))
        future = SpatialRecord("public-weather", "r2", "2026-01-01T00:02:00Z", "2026-01-01T00:01:00Z")
        self.assertTrue(any("later than fetched_at" in error for error in future.validate(self.registry)))

    def test_estimated_data_cannot_claim_current_freshness(self):
        record = SpatialRecord(
            "public-weather", "r1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z",
            freshness=Freshness.CURRENT, confidence=0.8, is_estimated=True,
        )
        self.assertTrue(any("estimated records" in error for error in record.validate(self.registry)))

    def test_sensitivity_requires_declared_enum(self):
        source = SpatialSource(
            "private-feed", "Provider", "1", "devices", "1", LicensePolicy("unknown"),
            sensitivity="private",  # type: ignore[arg-type]
        )
        self.assertTrue(any("sensitivity" in error for error in validate_source(source)))


if __name__ == "__main__":
    unittest.main()
