import unittest
from types import SimpleNamespace

from automation.reviewer_team import (
    REVIEWER_CAPABILITIES, ReviewerTeamError, build_reviewer_team,
)


class ReviewerTeamTests(unittest.TestCase):
    def test_assigns_distinct_enabled_reviewers_and_excludes_author(self):
        agents = [
            SimpleNamespace(agent_id="builder", enabled=True,
                            capabilities=frozenset(REVIEWER_CAPABILITIES.values())),
            *[
                SimpleNamespace(agent_id="reviewer-" + check, enabled=True,
                                capabilities=frozenset({capability}))
                for check, capability in REVIEWER_CAPABILITIES.items()
            ],
        ]
        plan = build_reviewer_team(
            artifact_sha256="a" * 64, author_agent_id="builder", agents=agents
        )
        self.assertEqual(len(plan.assignments), len(REVIEWER_CAPABILITIES))
        reviewer_ids = [item.reviewer_agent_id for item in plan.assignments]
        self.assertEqual(len(reviewer_ids), len(set(reviewer_ids)))
        self.assertNotIn("builder", reviewer_ids)

    def test_fails_closed_when_no_independent_specialist_exists(self):
        agents = [
            SimpleNamespace(agent_id="builder", enabled=True,
                            capabilities=frozenset(REVIEWER_CAPABILITIES.values()))
        ]
        with self.assertRaisesRegex(ReviewerTeamError, "independent_reviewer_unavailable"):
            build_reviewer_team(
                artifact_sha256="a" * 64, author_agent_id="builder", agents=agents
            )

    def test_rejects_duplicate_or_unsupported_checks(self):
        agents = [
            SimpleNamespace(agent_id="reviewer", enabled=True,
                            capabilities=frozenset(REVIEWER_CAPABILITIES.values()))
        ]
        with self.assertRaisesRegex(ReviewerTeamError, "unsupported_or_duplicate"):
            build_reviewer_team(
                artifact_sha256="a" * 64, author_agent_id="builder",
                agents=agents, required_checks=("security", "security"),
            )

    def test_disabled_reviewers_are_not_selected(self):
        agents = [
            SimpleNamespace(agent_id="disabled", enabled=False,
                            capabilities=frozenset(REVIEWER_CAPABILITIES.values()))
        ]
        with self.assertRaises(ReviewerTeamError):
            build_reviewer_team(
                artifact_sha256="a" * 64, author_agent_id="builder", agents=agents
            )


if __name__ == "__main__":
    unittest.main()
