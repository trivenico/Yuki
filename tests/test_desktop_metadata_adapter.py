import unittest

from core.desktop_metadata_adapter import DesktopMetadataAdapter
from core.observation_policy import ObservationPolicy, ObservationSession
from core.observation_sources import ObservationGateway


class DesktopMetadataAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = DesktopMetadataAdapter(
            allowed_app_ids=("com.example.editor",),
            allowed_browser_domains=("docs.example.com",),
            capacity=2,
        )
        self.session = ObservationSession(
            ObservationPolicy(
                enabled=True,
                allowed_sources=("desktop:metadata",),
            ),
            consent_granted=True,
        )

    def tearDown(self):
        self.adapter.close()

    def test_only_allowlisted_app_identifier_is_emitted(self):
        self.assertTrue(self.adapter.publish_app_activity("COM.EXAMPLE.EDITOR"))
        self.assertFalse(self.adapter.publish_app_activity("com.example.password-manager"))
        received = []
        self.assertTrue(ObservationGateway(self.session).poll(self.adapter, received.append))
        self.assertEqual(dict(received[0].metadata), {"application_id": "com.example.editor"})

    def test_only_allowlisted_browser_domain_is_emitted(self):
        self.assertTrue(self.adapter.publish_browser_domain("DOCS.EXAMPLE.COM."))
        self.assertFalse(self.adapter.publish_browser_domain("bank.example.net"))
        received = []
        self.assertTrue(ObservationGateway(self.session).poll(self.adapter, received.append))
        self.assertEqual(dict(received[0].metadata), {"domain": "docs.example.com"})

    def test_empty_allowlists_capture_nothing(self):
        adapter = DesktopMetadataAdapter()
        try:
            self.assertFalse(adapter.publish_app_activity("com.example.editor"))
            self.assertFalse(adapter.publish_browser_domain("docs.example.com"))
        finally:
            adapter.close()

    def test_adapter_rejects_invalid_domain_configuration(self):
        with self.assertRaises(ValueError):
            DesktopMetadataAdapter(allowed_browser_domains=("https://example.com/path",))

    def test_queue_is_bounded_and_close_is_terminal(self):
        self.assertTrue(self.adapter.publish_app_activity("com.example.editor"))
        self.assertTrue(self.adapter.publish_app_activity("com.example.editor"))
        self.assertFalse(self.adapter.publish_app_activity("com.example.editor"))
        self.adapter.close()
        self.assertFalse(self.adapter.publish_app_activity("com.example.editor"))


if __name__ == "__main__":
    unittest.main()
