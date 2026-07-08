from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str | None = None


@dataclass(frozen=True)
class TerminalSafetyRules:
    allow_shell: bool
    whitelist_commands: list[str]
    blacklist_patterns: list[str]
    confirm_patterns: list[str]


class SafetyManager:
    def __init__(self, rules: TerminalSafetyRules) -> None:
        self._rules = rules
        self._blacklist = [re.compile(pat) for pat in rules.blacklist_patterns]
        self._confirm = [re.compile(pat) for pat in rules.confirm_patterns]

    def evaluate_command(self, command: str) -> SafetyDecision:
        if not command.strip():
            return SafetyDecision(False, False, "Empty command is not allowed.")

        if self._uses_shell_features(command) and not self._rules.allow_shell:
            return SafetyDecision(False, False, "Shell features are disabled by policy.")

        for pattern in self._blacklist:
            if pattern.search(command):
                return SafetyDecision(False, False, "Command is explicitly blocked.")

        try:
            parts = shlex.split(command)
        except ValueError:
            return SafetyDecision(False, False, "Command parsing failed.")

        if not parts:
            return SafetyDecision(False, False, "Command parsing failed.")

        if self._rules.whitelist_commands:
            command_name = parts[0].split("/")[-1]
            if command_name not in self._rules.whitelist_commands:
                return SafetyDecision(False, False, "Command is not in whitelist.")

        requires_confirmation = any(pat.search(command) for pat in self._confirm)
        return SafetyDecision(True, requires_confirmation)

    @staticmethod
    def _uses_shell_features(command: str) -> bool:
        return any(token in command for token in ("|", ">", "<", "&&", "||", ";"))
