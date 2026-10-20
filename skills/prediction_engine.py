"""Structured prediction aggregation; requires user/model-supplied forecasts, no fabricated agents."""

import json
from skills.base import Skill, SkillResult


class PredictionEngineSkill(Skill):
    name = "prediction_engine"
    description = (
        "Aggregate supplied agent forecasts and surface disagreement, assumptions, and uncertainty."
    )
    risk = "medium"

    def run(self, argument=""):
        try:
            obj = json.loads(argument)
        except (ValueError, TypeError):
            return SkillResult(
                False,
                'Provide JSON: {"question":"...","forecasts":[{"agent":"base","probability":0.6,"rationale":"..."}]}.',
            )
        forecasts = obj.get("forecasts") if isinstance(obj, dict) else None
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("question"), str)
            or not obj["question"].strip()
            or not isinstance(forecasts, list)
            or not 2 <= len(forecasts) <= 12
        ):
            return SkillResult(
                False,
                "Question and 2–12 supplied forecasts are required; Yuki will not invent independent agents.",
            )
        vals = []
        for item in forecasts:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("agent"), str)
                or not isinstance(item.get("probability"), (int, float))
                or isinstance(item.get("probability"), bool)
                or not 0 <= item["probability"] <= 1
            ):
                return SkillResult(
                    False, "Each forecast needs an agent label and probability from 0 to 1."
                )
            vals.append(float(item["probability"]))
        mean = sum(vals) / len(vals)
        spread = max(vals) - min(vals)
        return SkillResult(
            True,
            "Aggregated supplied forecasts only; this is not an independently generated or validated prediction.",
            {
                "question": obj["question"][:500],
                "forecast_count": len(vals),
                "mean_probability": round(mean, 4),
                "range": [min(vals), max(vals)],
                "disagreement_range": round(spread, 4),
                "uncertainty_note": "Forecast quality depends on source independence, calibration, and evidence; no causal or black-swan validation was performed.",
                "forecasts": forecasts,
            },
        )
