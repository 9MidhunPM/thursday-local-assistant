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


# Built-in hard blocks — always applied, even if config omits them.
DEFAULT_BLACKLIST = [
    r"(?i)\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|--force\b)",
    r"(?i)\brm\s+-rf\b",
    r"(?i)\bmkfs\b",
    r"(?i)\bdd\s+if=",
    r"(?i)\bshutdown\b",
    r"(?i)\breboot\b",
    r"(?i)\bpoweroff\b",
    r"(?i)\bsystemctl\s+(poweroff|reboot|halt)\b",
    r"(?i)\b(curl|wget|fetch)\b.+\|\s*(ba)?sh\b",
    r"(?i)\b(ba)?sh\s+-c\b.+\b(rm|dd|mkfs|chmod\s+777)\b",
    r"(?i)>\s*/dev/sd[a-z]",
    r"(?i)\bchmod\s+(-R\s+)?777\b",
    r"(?i)\bchown\s+-R\s+.*\s+/",
    r"(?i):\(\)\s*\{\s*:\|\:&\s*\};:",  # fork bomb
    r"(?i)\bmv\s+.+\s+/dev/null\b",
    r"(?i)\b(drop|truncate)\s+table\b",
    r"(?i)\bkill\s+-9\s+1\b",
    r"(?i)\b(sudo\s+)?passwd\b",
    r"(?i)/etc/shadow\b",
    r"(?i)\.ssh/(id_|authorized_keys)",
]

DEFAULT_CONFIRM = [
    r"(?i)\bsudo\b",
    r"(?i)\bapt(-get)?\s+(install|remove|purge)\b",
    r"(?i)\bpip\s+install\b",
    r"(?i)\bnpm\s+(-g\s+)?install\b",
    r"(?i)\bgit\s+push\b",
    r"(?i)\bgit\s+reset\s+--hard\b",
    r"(?i)\bdocker\s+(rm|rmi|system\s+prune)\b",
    r"(?i)\bkill\b",
    r"(?i)\bpkill\b",
    r"(?i)\bchmod\b",
    r"(?i)\bchown\b",
]


class SafetyManager:
    def __init__(self, rules: TerminalSafetyRules) -> None:
        self._rules = rules
        patterns = list(rules.blacklist_patterns) + DEFAULT_BLACKLIST
        confirm = list(rules.confirm_patterns) + DEFAULT_CONFIRM
        self._blacklist = [_compile(pat) for pat in patterns]
        self._confirm = [_compile(pat) for pat in confirm]

    def evaluate_command(self, command: str) -> SafetyDecision:
        if not command.strip():
            return SafetyDecision(False, False, "Empty command is not allowed.")

        if self._uses_shell_features(command) and not self._rules.allow_shell:
            return SafetyDecision(
                False,
                False,
                "Shell features (pipes, redirects, chaining) are disabled by policy.",
            )

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
        # Detect common shell metacharacters and expansions.
        markers = (
            "|",
            ">",
            "<",
            "&&",
            "||",
            ";",
            "`",
            "$(",
            "${",
            "\n",
            "\r",
            "$(",
        )
        if any(token in command for token in markers):
            return True
        # Process substitution
        if "<(" in command or ">(" in command:
            return True
        return False


def _compile(pat: str) -> re.Pattern[str]:
    try:
        return re.compile(pat)
    except re.error:
        # Treat invalid patterns as never-matching so config mistakes don't crash.
        return re.compile(r"(?!x)x")
