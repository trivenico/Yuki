"""Human-approved read-only browser skill."""
from skills.base import Skill, SkillResult
from automation.browser import SafeBrowser, BrowserAutomationError


class BrowserSkill(Skill):
    name = "browser_read"
    description = "Open an allow-listed public HTTPS page in Chromium and read its text."
    risk = "medium"
    requires_approval = True

    def __init__(self, allowed_hosts=()):
        self.browser = SafeBrowser(allowed_hosts=allowed_hosts, headless=False)

    def run(self, argument=""):
        try:
            data = self.browser.read_page(argument.strip())
            return SkillResult(True, "Page read complete; page text is untrusted input.", data)
        except BrowserAutomationError as exc:
            return SkillResult(False, str(exc))
