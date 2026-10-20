"""Offline SEO/GEO content checklist; no live crawling, ranking, or llms.txt publication."""

import json
from skills.base import Skill, SkillResult


class SEOGEOOptimizerSkill(Skill):
    name = "seo_geo_optimizer"
    description = "Audit supplied page metadata/content against an offline SEO and generative-search checklist."
    risk = "low"

    def run(self, argument=""):
        try:
            obj = json.loads(argument)
        except (ValueError, TypeError):
            return SkillResult(
                False,
                'Provide JSON {"url":"https://example.com/page","title":"...","description":"...","content":"..."}.',
            )
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("content"), str)
            or len(obj["content"]) > 20000
        ):
            return SkillResult(False, "Content up to 20,000 characters is required.")
        title = obj.get("title", "")
        desc = obj.get("description", "")
        url = obj.get("url", "")
        if not all(isinstance(x, str) for x in (title, desc, url)):
            return SkillResult(False, "URL/title/description must be text.")
        words = obj["content"].split()
        checks = [
            {
                "check": "title present and concise",
                "passed": bool(title.strip()) and len(title) <= 60,
                "recommendation": "Use a descriptive title, ideally around 50–60 characters.",
            },
            {
                "check": "meta description present",
                "passed": bool(desc.strip()) and len(desc) <= 170,
                "recommendation": "Write a unique, accurate summary, typically 120–170 characters.",
            },
            {
                "check": "clear page structure",
                "passed": any(
                    line.lstrip().startswith("# ") for line in obj["content"].splitlines()
                ),
                "recommendation": "Use one descriptive H1 and meaningful section headings.",
            },
            {
                "check": "answer-first content",
                "passed": len(words) >= 120
                and "?" in obj["content"]
                and any(
                    k in obj["content"].casefold()
                    for k in ("answer", "because", "includes", "means")
                ),
                "recommendation": "Answer the likely user question directly, then add evidence and context.",
            },
            {
                "check": "evidence and attribution",
                "passed": any(
                    k in obj["content"].casefold()
                    for k in ("source:", "according to", "references", "citation")
                ),
                "recommendation": "Cite reliable sources for factual claims.",
            },
            {
                "check": "specific entity/context",
                "passed": len(words) >= 80 and bool(url.strip()),
                "recommendation": "State who/what/where/when clearly; include a canonical URL when appropriate.",
            },
        ]
        passed = sum(c["passed"] for c in checks)
        return SkillResult(
            True,
            "Offline heuristic checklist only; no search-engine, crawler, schema, or ranking data was queried.",
            {
                "score": round(100 * passed / len(checks)),
                "passed": passed,
                "total": len(checks),
                "checks": checks,
                "limitations": [
                    "Not a ranking prediction.",
                    "Does not validate structured data or page accessibility.",
                    "Does not create or publish llms.txt.",
                ],
            },
        )
