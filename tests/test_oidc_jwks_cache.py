import threading
import unittest

from core.oidc_jwks_cache import OIDCJWKSCache, OIDCJWKSCacheError


class OIDCJWKSCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.document = {"keys": [{"kid": "old"}]}
        self.calls = []
        def fetch(uri):
            self.calls.append(uri)
            return self.document
        self.cache = OIDCJWKSCache(
            fetcher=fetch, clock=lambda: self.now, ttl_seconds=30,
            maximum_entries=2, minimum_refresh_interval_seconds=5,
        )

    def test_caches_by_exact_uri_and_returns_defensive_copies(self):
        first = self.cache.get("https://issuer.example/jwks")
        first["keys"].clear()
        second = self.cache.get("https://issuer.example/jwks")
        self.assertEqual(second, {"keys": [{"kid": "old"}]})
        self.assertEqual(len(self.calls), 1)

    def test_expiry_refetches_and_never_serves_stale_data_on_failure(self):
        self.cache.get("https://issuer.example/jwks")
        self.now += 31
        self.cache._fetcher = lambda _uri: (_ for _ in ()).throw(OSError("network failure"))
        with self.assertRaisesRegex(OIDCJWKSCacheError, "jwks_unavailable"):
            self.cache.get("https://issuer.example/jwks")

    def test_unknown_key_forces_one_throttled_refresh(self):
        uri = "https://issuer.example/jwks"
        self.cache.get(uri)
        self.document = {"keys": [{"kid": "rotated"}]}
        throttled = self.cache.get(uri, force_refresh=True)
        self.assertEqual(throttled["keys"][0]["kid"], "old")
        self.now += 5
        refreshed = self.cache.get(uri, force_refresh=True)
        self.assertEqual(refreshed["keys"][0]["kid"], "rotated")
        self.assertEqual(len(self.calls), 2)

    def test_uri_entries_are_bounded_with_lru_eviction(self):
        self.cache.get("https://issuer.example/one")
        self.cache.get("https://issuer.example/two")
        self.cache.get("https://issuer.example/three")
        self.cache.get("https://issuer.example/one")
        self.assertEqual(len(self.calls), 4)

    def test_concurrent_initial_reads_share_one_fetch(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def fetch(uri):
            calls.append(uri)
            entered.set()
            release.wait(timeout=2)
            return {"keys": [{"kid": "one"}]}
        cache = OIDCJWKSCache(fetcher=fetch)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(cache.get("https://issuer.example/jwks")))
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(timeout=2))
        release.set()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 5)

    def test_rejects_invalid_configuration_and_jwks_documents(self):
        with self.assertRaises(ValueError):
            OIDCJWKSCache(fetcher=lambda _: {}, ttl_seconds=0)
        invalid = OIDCJWKSCache(fetcher=lambda _: {"keys": []})
        with self.assertRaisesRegex(OIDCJWKSCacheError, "jwks_invalid"):
            invalid.get("https://issuer.example/jwks")


if __name__ == "__main__":
    unittest.main()
