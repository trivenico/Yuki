"""Deterministic, non-causal evaluation of workforce improvement snapshots."""
from __future__ import annotations

import math


_METRICS = (
    ("success_rate", ("tasks", "succeeded"), ("tasks", "failed"), "higher"),
    ("failed_tasks", ("tasks", "failed"), None, "lower"),
    ("queued_tasks", ("tasks", "queued"), None, "lower"),
    ("independently_verified_successes", ("performance", "independently_verified_successes"), None, "higher"),
)


def _number(snapshot, path):
    value = snapshot
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value


def evaluate_improvement_snapshots(baseline, current, *, minimum_completed_observations=5):
    """Compare two bounded snapshots without treating temporal differences as causal proof."""
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        raise ValueError("baseline and current snapshots must be objects")
    if type(minimum_completed_observations) is not int or not 1 <= minimum_completed_observations <= 10000:
        raise ValueError("minimum_completed_observations must be an integer in 1..10000")

    def completed(snapshot):
        succeeded = _number(snapshot, ("tasks", "succeeded"))
        failed = _number(snapshot, ("tasks", "failed"))
        if succeeded is None or failed is None:
            return None
        return succeeded + failed

    data_quality_issues = []
    for label, snapshot in (("baseline", baseline), ("current", current)):
        succeeded = _number(snapshot, ("tasks", "succeeded"))
        failed = _number(snapshot, ("tasks", "failed"))
        queued = _number(snapshot, ("tasks", "queued"))
        verified = _number(snapshot, ("performance", "independently_verified_successes"))
        for metric_name, value in (("succeeded", succeeded), ("failed", failed), ("queued", queued),
                                   ("independently_verified_successes", verified)):
            if value is not None and not float(value).is_integer():
                data_quality_issues.append({
                    "snapshot": label, "metric": metric_name, "code": "non_integer_task_count",
                })
        if succeeded is not None and verified is not None and verified > succeeded:
            data_quality_issues.append({
                "snapshot": label, "metric": "independently_verified_successes",
                "code": "verified_successes_exceed_succeeded_tasks",
            })
    baseline_n = completed(baseline)
    current_n = completed(current)
    enough = (
        baseline_n is not None and current_n is not None
        and baseline_n >= minimum_completed_observations
        and current_n >= minimum_completed_observations
    )
    metrics = {}
    for name, numerator_path, denominator_path, preferred in _METRICS:
        before = _number(baseline, numerator_path)
        after = _number(current, numerator_path)
        if name == "success_rate":
            before_den = _number(baseline, denominator_path)
            after_den = _number(current, denominator_path)
            if before is None or after is None or before_den is None or after_den is None:
                before = after = None
            else:
                before_total, after_total = before + before_den, after + after_den
                before = before / before_total if before_total else None
                after = after / after_total if after_total else None
        if name == "independently_verified_successes":
            baseline_succeeded = _number(baseline, ("tasks", "succeeded"))
            current_succeeded = _number(current, ("tasks", "succeeded"))
            if ((baseline_succeeded is not None and before is not None and before > baseline_succeeded)
                    or (current_succeeded is not None and after is not None and after > current_succeeded)):
                before = after = None
        if before is None or after is None:
            metrics[name] = {"available": False, "status": "unavailable"}
            continue
        delta = after - before
        if delta == 0:
            status = "unchanged"
        elif (delta > 0 and preferred == "higher") or (delta < 0 and preferred == "lower"):
            status = "favorable_direction"
        else:
            status = "unfavorable_direction"
        metrics[name] = {
            "available": True,
            "baseline": before,
            "current": after,
            "delta": delta,
            "preferred_direction": preferred,
            "status": status if enough else "insufficient_observations",
        }

    return {
        "status": "descriptive_comparison" if enough else "insufficient_observations",
        "minimum_completed_observations": minimum_completed_observations,
        "baseline_completed_observations": baseline_n,
        "current_completed_observations": current_n,
        "metrics": metrics,
        "data_quality": {
            "status": "warnings" if data_quality_issues else "no_detected_count_inconsistencies",
            "issues": data_quality_issues,
            "note": "Count consistency checks are partial; unavailable metrics are not inferred or repaired.",
        },
        "interpretation": (
            "Directional differences between bounded snapshots only. They do not establish "
            "causation, intervention effectiveness, statistical significance, or durable improvement. "
            "Task mix, staffing, overlapping goal windows, and other operational changes may explain differences."
        ),
        "human_review_required": True,
        "automatic_action_taken": False,
    }
