"""Human-approved read-only browser skill."""
from skills.base import Skill, SkillResult
from automation.browser import SafeBrowser, BrowserAutomationError
from core.prompt_guard import scan_untrusted_text


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
            data["prompt_injection_scan"] = scan_untrusted_text(data.get("text", ""))
            return SkillResult(True, "Page read complete; page text is untrusted input.", data)
        except BrowserAutomationError as exc:
            return SkillResult(False, str(exc))
