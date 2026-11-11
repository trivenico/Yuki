"""Fail-closed tests for relationship mode settings."""
import unittest

from companion.relationship_mode import AffectionLevel, RelationshipMode, RelationshipModeSettings


class RelationshipModeSettingsTests(unittest.TestCase):
    def test_defaults_are_off_and_have_no_memory_or_proactive_checkins(self):
        settings = RelationshipModeSettings()
        self.assertIs(settings.mode, RelationshipMode.OFF)
        self.assertFalse(settings.adult_confirmed)
        self.assertFalse(settings.memory_enabled)
        self.assertFalse(settings.proactive_checkins_enabled)
        self.assertEqual(settings.persona_guidance(), "")

    def test_romantic_mode_requires_adult_confirmation(self):
        with self.assertRaisesRegex(ValueError, "adult confirmation"):
            RelationshipModeSettings(mode=RelationshipMode.ROMANTIC)

    def test_explicit_adult_confirmation_allows_romantic_style_context(self):
        settings = RelationshipModeSettings(
            mode=RelationshipMode.ROMANTIC,
            adult_confirmed=True,
            affection=AffectionLevel.HIGH,
        )
        guidance = settings.persona_guidance()
        self.assertIn("romantic-companion tone", guidance)
        self.assertIn("Remain honest", guidance)
        self.assertIn("never grants tools", guidance)

    def test_friendly_mode_does_not_require_adult_confirmation(self):
        settings = RelationshipModeSettings(mode=RelationshipMode.FRIENDLY)
        self.assertIn("friendly", settings.persona_guidance())

    def test_memory_and_checkins_are_independent_optins(self):
        with self.assertRaisesRegex(ValueError, "enabled mode"):
            RelationshipModeSettings(memory_enabled=True)
        with self.assertRaisesRegex(ValueError, "enabled mode"):
            RelationshipModeSettings(proactive_checkins_enabled=True)

    def test_checkins_require_quiet_hours(self):
        with self.assertRaisesRegex(ValueError, "quiet-hour"):
            RelationshipModeSettings(
                mode=RelationshipMode.FRIENDLY,
                proactive_checkins_enabled=True,
            )
        settings = RelationshipModeSettings(
            mode=RelationshipMode.FRIENDLY,
            proactive_checkins_enabled=True,
            quiet_hours_start="22:00",
            quiet_hours_end="08:00",
        )
        self.assertTrue(settings.proactive_checkins_enabled)

    def test_invalid_quiet_hours_are_rejected(self):
        for value in ("24:00", "12:60", "9:00", "noon", "12-00"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "HH:MM"):
                    RelationshipModeSettings(
                        mode=RelationshipMode.FRIENDLY,
                        quiet_hours_start=value,
                    )

    def test_topic_preferences_are_bounded_and_validated(self):
        with self.assertRaisesRegex(ValueError, "topics_to_avoid"):
            RelationshipModeSettings(topics_to_avoid=("",))
        with self.assertRaisesRegex(ValueError, "topics_to_avoid"):
            RelationshipModeSettings(topics_to_avoid=("x" * 121,))

    def test_relationship_guidance_does_not_claim_authority(self):
        settings = RelationshipModeSettings(
            mode=RelationshipMode.ROMANTIC,
            adult_confirmed=True,
            topics_to_avoid=("work stress",),
        )
        guidance = settings.persona_guidance()
        self.assertIn("Do not initiate discussion of: work stress.", guidance)
        self.assertIn("overrides safety policy", guidance)
