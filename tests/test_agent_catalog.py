import copy
import json
import tempfile
import unittest
from pathlib import Path

from automation.agent_catalog import load_agent_catalog, validate_agent_catalog


class AgentCatalogTests(unittest.TestCase):
    def test_checked_in_catalog_has_exactly_100_non_executable_roles(self):
        catalog = load_agent_catalog()
        self.assertEqual(len(catalog["agents"]), 100)
        self.assertEqual(len({item["agent_id"] for item in catalog["agents"]}), 100)
        self.assertEqual(
            {item["division"] for item in catalog["agents"]},
            {
                "Personal AI orchestration and knowledge",
                "Research, web, and knowledge discovery",
                "Software engineering and technical delivery",
                "Data, analytics, and AI/ML",
                "Business building and entrepreneurship",
                "Content, communication, and creative production",
                "Personal productivity, learning, and accessibility",
                "Finance, procurement, and operations analysis",
                "Devices, desktop, and connected environments",
                "Security, quality, governance, and reliability",
            },
        )
        self.assertTrue(all(item["lifecycle_state"] == "proposed" for item in catalog["agents"]))
        self.assertTrue(all(not item["required_permissions"] for item in catalog["agents"]))
        self.assertTrue(all(not item["allowed_tool_classes"] for item in catalog["agents"]))
        self.assertTrue(all(item["handler_ref"] is None for item in catalog["agents"]))
        self.assertTrue(all(item["evaluation_ref"] is None for item in catalog["agents"]))

    def test_duplicate_agent_ids_are_rejected(self):
        catalog = copy.deepcopy(load_agent_catalog())
        catalog["agents"][1]["agent_id"] = catalog["agents"][0]["agent_id"]
        self.assertTrue(any("duplicated" in error for error in validate_agent_catalog(catalog)))

    def test_proposed_role_cannot_be_granted_tools_or_permissions(self):
        catalog = copy.deepcopy(load_agent_catalog())
        catalog["agents"][0]["required_permissions"] = ["workspace:write"]
        self.assertTrue(any("cannot receive permissions" in error
                            for error in validate_agent_catalog(catalog)))

    def test_enabled_role_requires_handler_evaluation_and_explicit_scope(self):
        catalog = copy.deepcopy(load_agent_catalog())
        catalog["agents"][0]["lifecycle_state"] = "enabled"
        errors = validate_agent_catalog(catalog)
        self.assertTrue(any("handler and evaluation" in error for error in errors))
        self.assertTrue(any("permissions and tool classes" in error for error in errors))

    def test_loader_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be loaded"):
                load_agent_catalog(path)


if __name__ == "__main__":
    unittest.main()
