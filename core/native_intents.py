"""Deterministic, side-effect-free intent parsing for Yuki's native core.

This module classifies explicit local commands. It does not read or mutate
memory, enqueue tasks, invoke tools, access the network, or grant permissions.
Dispatch remains the responsibility of an authenticated application service.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import shlex


class IntentKind(str, Enum):
    HELP = "help"
    STATUS = "status"
    MEMORY_SEARCH = "memory.search"
    MEMORY_REMEMBER = "memory.remember"
    TASK_LIST = "task.list"
    TASK_SHOW = "task.show"
    PLAN_CREATE = "plan.create"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NativeIntent:
    """Immutable parse result; arguments are data, never executable actions."""

    kind: IntentKind
    arguments: tuple[str, ...] = ()
    requires_confirmation: bool = False
    reason: str | None = None


class NativeIntentParser:
    """Parse a deliberately small, explicit command language."""

    MAX_INPUT_CHARS = 4_000
    MAX_ARGUMENT_CHARS = 2_000
    MAX_ARGUMENTS = 16
    _TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

    def parse(self, text: str) -> NativeIntent:
        if not isinstance(text, str):
            raise TypeError("intent input must be text.")
        if not text.strip():
            return NativeIntent(IntentKind.UNKNOWN, reason="empty_input")
        if len(text) > self.MAX_INPUT_CHARS:
            return NativeIntent(IntentKind.UNKNOWN, reason="input_too_long")
        if "\x00" in text or any(ord(char) < 32 and char not in "\t\r\n" for char in text):
            return NativeIntent(IntentKind.UNKNOWN, reason="control_character")

        try:
            tokens = shlex.split(text.strip(), posix=True)
        except ValueError:
            return NativeIntent(IntentKind.UNKNOWN, reason="invalid_quoting")
        if not tokens:
            return NativeIntent(IntentKind.UNKNOWN, reason="empty_input")
        if len(tokens) > self.MAX_ARGUMENTS + 2:
            return NativeIntent(IntentKind.UNKNOWN, reason="too_many_arguments")
        if any(len(token) > self.MAX_ARGUMENT_CHARS for token in tokens):
            return NativeIntent(IntentKind.UNKNOWN, reason="argument_too_long")

        command = tokens[0].casefold()
        args = tokens[1:]
        if command in {"/help", "help"} and not args:
            return NativeIntent(IntentKind.HELP)
        if command in {"/status", "status"} and not args:
            return NativeIntent(IntentKind.STATUS)
        if command in {"/memory", "memory"}:
            return self._memory(args)
        if command in {"/task", "task"}:
            return self._task(args)
        if command in {"/plan", "plan"}:
            goal = " ".join(args).strip()
            if not goal:
                return NativeIntent(IntentKind.UNKNOWN, reason="plan_goal_required")
            return NativeIntent(
                IntentKind.PLAN_CREATE, (goal,), requires_confirmation=True
            )
        return NativeIntent(IntentKind.UNKNOWN, reason="unsupported_command")

    @staticmethod
    def _memory(args: list[str]) -> NativeIntent:
        if not args:
            return NativeIntent(IntentKind.UNKNOWN, reason="memory_action_required")
        action = args[0].casefold()
        value = " ".join(args[1:]).strip()
        if action == "search" and value:
            return NativeIntent(IntentKind.MEMORY_SEARCH, (value,))
        if action == "remember" and value:
            return NativeIntent(
                IntentKind.MEMORY_REMEMBER, (value,), requires_confirmation=True
            )
        if action in {"search", "remember"}:
            return NativeIntent(
                IntentKind.UNKNOWN, reason=f"memory_{action}_query_required"
            )
        return NativeIntent(IntentKind.UNKNOWN, reason="unsupported_memory_action")

    @classmethod
    def _task(cls, args: list[str]) -> NativeIntent:
        if not args:
            return NativeIntent(IntentKind.UNKNOWN, reason="task_action_required")
        action = args[0].casefold()
        if action == "list" and len(args) == 1:
            return NativeIntent(IntentKind.TASK_LIST)
        if action == "show" and len(args) == 2 and cls._TASK_ID.fullmatch(args[1]):
            return NativeIntent(IntentKind.TASK_SHOW, (args[1],))
        if action == "show":
            return NativeIntent(IntentKind.UNKNOWN, reason="valid_task_id_required")
        return NativeIntent(IntentKind.UNKNOWN, reason="unsupported_task_action")


__all__ = ["IntentKind", "NativeIntent", "NativeIntentParser"]
