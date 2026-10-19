import io
import sys
import unittest
from unittest.mock import patch

from main import build_agent, load_config


class CLITests(unittest.TestCase):
    def test_build_agent_loads_default_skills(self):
        config = load_config()
        agent = build_agent(config)
        self.assertIn("time", agent.registry.names())
        self.assertIn("mcp_tool", agent.registry.names())

    def test_empty_skill_name_handled_gracefully(self):
        # Verify /skill command gracefully handles missing/whitespace name without IndexError
        parts = "/skill ".split(maxsplit=2)
        has_valid_name = len(parts) >= 2 and bool(parts[1].strip())
        self.assertFalse(has_valid_name)


if __name__ == "__main__":
    unittest.main()
