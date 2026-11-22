"""Tests for provider-independent native intent routing."""
import unittest

from core.native_intelligence import NativeIntentRouter
from skills.catalog import SkillCard


def card(name, *, tags=(), description="", usage="", examples=()):
    return SkillCard(
        name=name, description=description, risk="low", tags=tuple(tags),
        usage=usage, agent_guidance="", examples=tuple(examples),
        requires_approval=False,
    )


class NativeIntentRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = NativeIntentRouter()

    def test_synonym_routes_without_a_model_provider(self):
        result = self.router.resolve("locate project documents", [
            card("workspace_search", tags=("files", "search", "research"),
                 description="Search project files and workspace documents"),
            card("time", tags=("clock", "date")),
        ])
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.selected.name, "workspace_search")

    def test_router_abstains_when_capabilities_are_tied(self):
        result = self.router.resolve("search files", [
            card("workspace_search", tags=("search", "files")),
            card("document_search", tags=("search", "files")),
        ])
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.selected)
        self.assertEqual(result.reason, "competing_capabilities")

    def test_unknown_intent_does_not_guess(self):
        result = self.router.resolve("bake sourdough bread", [
            card("workspace_search", tags=("files", "search")),
        ])
        self.assertEqual(result.status, "no_match")
        self.assertIsNone(result.selected)

    def test_query_validation_and_unicode_normalization(self):
        self.assertEqual(self.router.resolve("   ", []).status, "no_match")
        result = self.router.resolve("ＦＩＮＤ project files", [
            card("workspace_search", tags=("search", "documents", "files")),
        ])
        self.assertEqual(result.status, "matched")

    def test_rankings_are_stable(self):
        cards = [
            card("z_search", tags=("files", "search")),
            card("a_search", tags=("files", "search")),
        ]
        first = self.router.resolve("search files", cards)
        second = self.router.resolve("search files", reversed(cards))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
