from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from assistant.logging_utils import Loggers
from assistant.memory.long_term import LongTermMemory


@dataclass(frozen=True)
class ExecutionContext:
    confirm: Callable[[str], bool]
    loggers: Loggers
    memory: LongTermMemory
    now: Callable[[], datetime]
    analyze_images: Callable[[str, list[Path]], str] | None = None
    summarize_private_text: Callable[[str], str] | None = None
    report_progress: Callable[[str], None] | None = None
