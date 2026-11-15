import unittest

from core.tool_capabilities import (
    ToolCapability,
    ToolCapabilityCatalog,
    ToolCapabilityError,
    ToolEffect,
)


class ToolCapabilityCatalogTests(unittest.TestCase):
    def test_resolves_explicit_read_only_capability(self):
        catalog = ToolCapabilityCatalog({
            "search_files": ToolCapability(ToolEffect.READ_ONLY),
        })
        capability = catalog.resolve("search_files")
        self.assertIs(capability.effect, ToolEffect.READ_ONLY)
        self.assertEqual(catalog.names(), ("search_files",))

    def test_side_effecting_tool_requires_host_authored_metadata(self):
        catalog = ToolCapabilityCatalog({
            "send_email": ToolCapability(
                ToolEffect.SIDE_EFFECTING,
                requires_user_confirmation=True,
                provider_idempotency=True,
                supports_reconciliation=True,
            ),
        })
        capability = catalog.require_effect("send_email", ToolEffect.SIDE_EFFECTING)
        self.assertTrue(capability.requires_user_confirmation)
        self.assertTrue(capability.provider_idempotency)
        self.assertTrue(capability.supports_reconciliation)

    def test_unknown_tool_fails_closed(self):
        catalog = ToolCapabilityCatalog({})
        with self.assertRaises(ToolCapabilityError):
            catalog.resolve("send_email")

    def test_effect_mismatch_is_rejected(self):
        catalog = ToolCapabilityCatalog({
            "send_email": ToolCapability(ToolEffect.SIDE_EFFECTING),
        })
        with self.assertRaises(ToolCapabilityError):
            catalog.require_effect("send_email", ToolEffect.READ_ONLY)

    def test_catalog_is_immutable_and_does_not_retain_mutable_input(self):
        source = {"read": ToolCapability(ToolEffect.READ_ONLY)}
        catalog = ToolCapabilityCatalog(source)
        source["write"] = ToolCapability(ToolEffect.SIDE_EFFECTING)
        with self.assertRaises(ToolCapabilityError):
            catalog.resolve("write")
        with self.assertRaises(TypeError):
            catalog._capabilities["write"] = ToolCapability(ToolEffect.SIDE_EFFECTING)

    def test_invalid_names_and_metadata_are_rejected(self):
        for name in ("", "../send", "x" * 129, 12):
            with self.subTest(name=name), self.assertRaises(ToolCapabilityError):
                ToolCapabilityCatalog({name: ToolCapability(ToolEffect.READ_ONLY)})
        with self.assertRaises(ToolCapabilityError):
            ToolCapabilityCatalog({"send": object()})

    def test_read_only_capability_cannot_claim_side_effect_controls(self):
        with self.assertRaises(ToolCapabilityError):
            ToolCapability(
                ToolEffect.READ_ONLY,
                requires_user_confirmation=True,
            )

    def test_boolean_fields_reject_integer_lookalikes(self):
        with self.assertRaises(ToolCapabilityError):
            ToolCapability(ToolEffect.SIDE_EFFECTING, requires_user_confirmation=1)

    def test_effect_must_be_an_enum_not_a_model_supplied_string(self):
        with self.assertRaises(ToolCapabilityError):
            ToolCapability("side_effecting")


if __name__ == "__main__":
    unittest.main()
