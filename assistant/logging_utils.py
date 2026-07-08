from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Loggers:
    user: logging.Logger
    model: logging.Logger
    tool: logging.Logger
    error: logging.Logger


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "exc_info", "exc_text",
                "stack_info", "getMessage", "message", "asctime"
            }:
                log_data[key] = value

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


def _build_logger(name: str, level: int, log_file: Path, structured: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger

    handler = logging.FileHandler(log_file, encoding="utf-8")
    if structured:
        formatter = StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def setup_logging(directory: Path, level: str, structured: bool = False) -> Loggers:
    directory.mkdir(parents=True, exist_ok=True)
    level_value = logging.getLevelName(level.upper())
    if isinstance(level_value, str):
        level_value = logging.INFO
    return Loggers(
        user=_build_logger("assistant.user", level_value, directory / "user.log", structured),
        model=_build_logger("assistant.model", level_value, directory / "model.log", structured),
        tool=_build_logger("assistant.tool", level_value, directory / "tool.log", structured),
        error=_build_logger("assistant.error", level_value, directory / "error.log", structured),
    )


# Structured logging helpers
def log_model_interaction(
    logger: logging.Logger,
    user_text: str,
    response: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Log model interaction with structured data."""
    extra = {"user_text": user_text}
    if response:
        extra["response"] = response
    if tool_calls:
        extra["tool_calls"] = tool_calls
    logger.info("model_interaction", extra=extra)


def log_tool_call(
    logger: logging.Logger,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Log tool invocation with structured data."""
    logger.info("tool_call", extra={"tool": tool_name, "arguments": arguments})


def log_tool_result(
    logger: logging.Logger,
    tool_name: str,
    success: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Log tool result with structured data."""
    extra = {"tool": tool_name, "success": success}
    if result:
        extra["result_keys"] = list(result.keys())
    if error:
        extra["error"] = error
        logger.error("tool_result", extra=extra)
    else:
        logger.info("tool_result", extra=extra)


def log_error(
    logger: logging.Logger,
    message: str,
    error: Exception | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Log error with structured context."""
    extra = {"message": message}
    if error:
        extra["error_type"] = type(error).__name__
        extra["error_message"] = str(error)
    if context:
        extra["context"] = context
    logger.error("error", extra=extra)