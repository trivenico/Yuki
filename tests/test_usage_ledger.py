import tempfile
import unittest
from pathlib import Path

from core.usage_ledger import UsageConflict, UsageLedger


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = UsageLedger(Path(self.temp.name) / "usage.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_records_exact_cost_with_decimal_arithmetic(self):
        result = self.ledger.record(
            provider="openai", model="model-a", session_id="s1",
            request_id="req-1", input_tokens=1500, output_tokens=500,
            input_price_per_million="0.10", output_price_per_million="0.40",
            currency="USD", price_version="catalog-2026-11-06",
        )
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["event"]["cost"], "0.00035")
        self.assertEqual(self.ledger.summary(session_id="s1")["cost_by_currency"]["USD"], "0.00035")

    def test_unknown_price_is_not_guessed(self):
        self.ledger.record(
            provider="local", model="local-model", input_tokens=10,
            output_tokens=20,
        )
        summary = self.ledger.summary()
        self.assertEqual(summary["unknown_cost_events"], 1)
        self.assertEqual(summary["cost_by_currency"], {})

    def test_request_id_is_idempotent_for_identical_usage(self):
        args = dict(
            provider="openai", model="model-a", input_tokens=10,
            output_tokens=5, request_id="same-request",
        )
        first = self.ledger.record(**args)
        second = self.ledger.record(**args)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["event"]["event_id"], second["event"]["event_id"])
        self.assertEqual(self.ledger.summary()["events"], 1)

    def test_idempotency_canonicalizes_equivalent_decimal_prices(self):
        common = dict(
            provider="openai", model="model-a", input_tokens=100,
            output_tokens=50, request_id="decimal-equivalent",
            currency="USD", price_version="v1",
        )
        first = self.ledger.record(
            **common, input_price_per_million="0.10",
            output_price_per_million="0.40",
        )
        second = self.ledger.record(
            **common, input_price_per_million="0.100",
            output_price_per_million="0.4000",
        )
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["event"]["event_id"], second["event"]["event_id"])

    def test_request_id_conflict_is_rejected(self):
        self.ledger.record(
            provider="openai", model="model-a", input_tokens=10,
            output_tokens=5, request_id="same-request",
        )
        with self.assertRaises(UsageConflict):
            self.ledger.record(
                provider="openai", model="model-a", input_tokens=11,
                output_tokens=5, request_id="same-request",
            )

    def test_price_metadata_requires_both_prices_and_version(self):
        with self.assertRaisesRegex(ValueError, "both_input_and_output_prices"):
            self.ledger.record(
                provider="openai", model="model-a", input_tokens=1,
                output_tokens=1, input_price_per_million="1",
                currency="USD", price_version="v1",
            )

    def test_rejects_boolean_token_counts_and_non_finite_prices(self):
        with self.assertRaises(ValueError):
            self.ledger.record(
                provider="openai", model="model-a", input_tokens=True,
                output_tokens=1,
            )
        with self.assertRaises(ValueError):
            self.ledger.record(
                provider="openai", model="model-a", input_tokens=1,
                output_tokens=1, input_price_per_million="NaN",
                output_price_per_million="1", currency="USD", price_version="v1",
            )

    def test_persists_across_ledger_instances(self):
        self.ledger.record(
            provider="ollama", model="local", input_tokens=7,
            output_tokens=8, session_id="persist",
        )
        reloaded = UsageLedger(Path(self.temp.name) / "usage.sqlite3")
        self.assertEqual(reloaded.summary(session_id="persist")["total_tokens"], 15)

    def test_summary_keeps_currencies_separate(self):
        for provider, currency, rate in (
            ("openai", "USD", "1"), ("provider-eu", "EUR", "2")
        ):
            self.ledger.record(
                provider=provider, model="m", input_tokens=1, output_tokens=0,
                input_price_per_million=rate, output_price_per_million=rate,
                currency=currency, price_version="v1",
            )
        self.assertEqual(set(self.ledger.summary()["cost_by_currency"]), {"USD", "EUR"})

    def test_list_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            self.ledger.list_events(limit=1001)


if __name__ == "__main__":
    unittest.main()
