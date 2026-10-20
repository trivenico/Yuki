"""Local manual ledger. No bank connection, trading, or financial advice."""

from pathlib import Path
from skills.base import Skill, SkillResult
from skills.safe_local import load_json, save_json, parse_object
import uuid


class FinancialManagerSkill(Skill):
    name = "financial_manager"
    description = "Record and summarize a manual local ledger; no bank sync, payment, or trading."
    risk = "high"

    def __init__(self, workspace):
        self.path = Path(workspace).resolve() / "manual_ledger.json"

    def run(self, argument=""):
        rows = load_json(self.path, [])
        if argument.strip() == "summary":
            return SkillResult(
                True,
                "Manual ledger summary only.",
                {
                    "entries": len(rows),
                    "net_minor_units": sum(int(r["amount_minor"]) for r in rows),
                },
            )
        if argument.strip() == "list":
            return SkillResult(True, "Recent manual ledger entries.", rows[-50:])
        obj = parse_object(argument)
        if (
            not obj
            or not isinstance(obj.get("description"), str)
            or not isinstance(obj.get("amount_minor"), int)
            or isinstance(obj.get("amount_minor"), bool)
        ):
            return SkillResult(
                False,
                'Use summary, list, or JSON {"description":"groceries","amount_minor":-1200,"currency":"GBP"}. Amount is integer minor units.',
            )
        desc = obj["description"].strip()
        currency = obj.get("currency", "GBP")
        if (
            not desc
            or len(desc) > 160
            or not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isalpha()
        ):
            return SkillResult(False, "Description or currency is invalid.")
        rows.append(
            {
                "id": uuid.uuid4().hex[:12],
                "description": desc,
                "amount_minor": obj["amount_minor"],
                "currency": currency.upper(),
            }
        )
        if not save_json(self.path, rows[-1000:]):
            return SkillResult(False, "Could not save ledger.")
        return SkillResult(
            True,
            "Recorded a manual ledger entry. No bank account was accessed.",
            {"entries": min(len(rows), 1000)},
        )
