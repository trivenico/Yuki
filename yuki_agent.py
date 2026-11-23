"""Yuki's native-first conversational entry point.

Optional model providers may generate language, but local registered skills and
their guarded execution path remain owned by Yuki.
"""
import json
import re

from core.planner import Planner


class YukiAgent:
    """Route to Yuki-native capabilities first when no model is selected."""

    def __init__(self, provider=None, native_agent=None, *, planner=None, memory_search=None):
        self.provider = None
        self.native_agent = None
        self.planner = planner or Planner()
        if not callable(getattr(self.planner, "decompose", None)) or not callable(
            getattr(self.planner, "propose", None)
        ):
            raise TypeError("planner must implement decompose() and propose().")
        if memory_search is not None and not callable(memory_search):
            raise TypeError("memory_search must be callable.")
        self.memory_search = memory_search
        if native_agent is not None:
            if not callable(getattr(native_agent, "run_skill_for_task", None)):
                raise TypeError("native_agent must implement run_skill_for_task().")
            self.native_agent = native_agent
        if provider is not None:
            self.set_provider(provider)

    def set_provider(self, provider):
        """Replace the active optional adapter after validating its interface."""
        if provider is None:
            self.provider = None
            return
        if not callable(getattr(provider, "generate", None)) or not callable(
            getattr(provider, "is_available", None)
        ):
            raise TypeError("Provider must implement generate() and is_available().")
        self.provider = provider

    def status(self):
        if self.provider is None:
            native = "native capabilities are enabled" if self.native_agent else "native core is ready"
            return f"Yuki {native}; no optional model provider is configured."
        try:
            return "Optional provider is ready." if self.provider.is_available() else "Optional provider is unavailable."
        except Exception:
            return "Provider status is unknown."

    @staticmethod
    def _is_plan_request(prompt):
        normalized = prompt.casefold()
        return bool(re.search(
            r"\b(plan|roadmap|break down|decompose|steps to|step by step)\b",
            normalized,
        ))

    def _ask_native_plan(self, prompt):
        if not self._is_plan_request(prompt):
            return None
        try:
            context = self.memory_search(prompt) if self.memory_search is not None else ()
            if context is None:
                context = ()
            if (
                not isinstance(context, (list, tuple))
                or any(not isinstance(item, str) for item in context[:10])
            ):
                raise TypeError("native memory search must return bounded text entries.")
            plan = self.planner.propose(prompt, memory_context=context)
            tasks = self.planner.decompose(prompt)
        except Exception:
            return "Yuki: I could not safely prepare a native plan from this request."
        lines = [
            "Yuki: Native plan proposal (nothing has been executed).",
            "Goal: " + plan.goal,
            "Task breakdown:",
        ]
        for index, task in enumerate(tasks, 1):
            dependencies = ", ".join(task.depends_on) if task.depends_on else "none"
            approval = "required" if task.requires_approval else "not required for reporting"
            lines.append(
                f"{index}. [{task.task_id}] {task.description} "
                f"(capability: {', '.join(task.required_capabilities)}; "
                f"depends on: {dependencies}; approval: {approval})"
            )
        lines.append("Execution sequence:")
        for step in plan.steps:
            lines.append(f"- {step.action}: {step.description}")
        if plan.memory_context:
            lines.append("Relevant saved lessons (reference only):")
            lines.extend(f"- {item}" for item in plan.memory_context)
        lines.append("This is a proposal only; task execution requires separate approval.")
        return "\n".join(lines)

    def _ask_native(self, prompt):
        if self.native_agent is None:
            return None
        try:
            result = self.native_agent.run_skill_for_task(prompt, "")
        except Exception:
            return "Yuki: Native capability routing encountered a recoverable error."
        if getattr(result, "stage", "") == "intent_ambiguous":
            return "Yuki: " + str(result.message)
        if getattr(result, "stage", "") == "permission_denied":
            return "Yuki: I did not execute that capability because approval was not granted."
        if not getattr(result, "ok", False):
            if getattr(result, "tool_name", None):
                return "Yuki: " + str(result.message)
            return None
        message = str(getattr(result, "message", "Native capability completed."))
        data = getattr(result, "data", None)
        if data is not None:
            try:
                rendered = json.dumps(data, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                rendered = str(data)
            return "Yuki: " + message + "\n" + rendered
        return "Yuki: " + message

    def ask(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        if len(prompt) > 20_000:
            return "Yuki: Please keep requests under 20000 characters."
        cleaned = prompt.strip()
        native_plan = self._ask_native_plan(cleaned)
        if native_plan is not None:
            return native_plan
        native_response = self._ask_native(cleaned)
        if native_response is not None:
            return native_response
        if self.provider is None:
            return (
                "Yuki native mode is active. Local registered capabilities, memory, "
                "and task workflows remain available; free-form language generation "
                "requires an explicitly installed local model."
            )
        try:
            if not self.provider.is_available():
                return (
                    "Yuki: The optional model provider is unavailable. "
                    "Native local capabilities remain available."
                )
            result = self.provider.generate(cleaned)
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception:
            return "Yuki: The optional provider encountered an error; native local capabilities remain available."
