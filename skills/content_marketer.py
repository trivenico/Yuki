"""Draft-only content marketing helper. No publishing, scraping or outreach."""

from skills.base import Skill, SkillResult


class ContentMarketerSkill(Skill):
    name = "content_marketer"
    description = "Create an ethical content brief or draft for human review."
    risk = "low"
    requires_approval = False

    def run(self, argument=""):
        topic = argument.strip()
        if not topic or len(topic) > 500:
            return SkillResult(False, "Provide a topic, audience and intended outcome.")
        return SkillResult(
            True,
            "Content brief prepared as a planning scaffold; no web research or publishing was performed.",
            {
                "topic": topic,
                "outline": [
                    "Audience and search intent",
                    "Evidence and sources to verify",
                    "Original useful angle",
                    "Draft outline",
                    "Fact-check and editorial review",
                    "Human-approved publishing",
                ],
            },
        )
