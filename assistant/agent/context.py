from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from assistant.logging_utils import Loggers
from assistant.memory.long_term import LongTermMemory


@dataclass(frozen=True)
class ExecutionContext:
    confirm: Callable[[str], bool]
    loggers: Loggers
    memory: LongTermMemory
    now: Callable[[], datetime]
