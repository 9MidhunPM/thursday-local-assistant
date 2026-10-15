from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from assistant.paths import private_dir
from assistant.security import redact_secrets


def redact_private_request_text(user_text: str) -> str:
    """Describe a request without retaining spoken or typed private content."""
    lowered = user_text.casefold()
    email_context = any(word in lowered for word in ("email", "gmail", "mail"))
    draft_action = any(word in lowered for word in ("compose", "draft", "send", "write"))
    inbox_action = any(word in lowered for word in ("inbox", "summar", "triage", "recent"))
    if email_context and draft_action:
        return "[email draft request redacted]"
    if email_context and inbox_action:
        return "[email inbox request redacted]"
    return f"[request content omitted; chars={len(user_text)}]"


@dataclass(frozen=True)
class Loggers:
    user: logging.Logger
    model: logging.Logger
    tool: logging.Logger
    error: logging.Logger


class PrivateFormatter(logging.Formatter):
    """Provider exception messages can contain request bodies and credentials."""

    def formatException(self, exc_info) -> str:
        return f"{exc_info[0].__name__}: [exception details omitted]"


class StructuredFormatter(PrivateFormatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "exc_info",
                "exc_text",
                "stack_info",
                "getMessage",
                "message",
                "asctime",
            }:
                log_data[key] = redact_secrets(value)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


def _build_logger(
    name: str, level: int, log_file: Path, structured: bool = False
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger

    handler = RotatingFileHandler(
        log_file,
        encoding="utf-8",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    if structured:
        formatter = StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    else:
        formatter = PrivateFormatter(
            fmt="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    log_file.chmod(0o600)
    return logger


def setup_logging(directory: Path, level: str, structured: bool = False) -> Loggers:
    private_dir(directory)
    level_value = logging.getLevelName(level.upper())
    if isinstance(level_value, str):
        level_value = logging.INFO
    return Loggers(
        user=_build_logger("assistant.user", level_value, directory / "user.log", structured),
        model=_build_logger("assistant.model", level_value, directory / "model.log", structured),
        tool=_build_logger("assistant.tool", level_value, directory / "tool.log", structured),
        error=_build_logger("assistant.error", level_value, directory / "error.log", structured),
    )


def log_model_interaction(
    logger: logging.Logger,
    user_text: str,
    response: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Keep useful request metrics without persisting conversation content."""
    extra: dict[str, Any] = {"request_chars": len(user_text)}
    if response is not None:
        extra["response_chars"] = len(response)
    if tool_calls:
        extra["tool_calls"] = [
            {
                "name": call.get("name", "unknown"),
                "argument_keys": sorted(call["arguments"])
                if isinstance(call.get("arguments"), dict) else [],
            }
            for call in tool_calls
        ]
    logger.info("model_interaction", extra=extra)


def log_tool_call(
    logger: logging.Logger,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Record which capability ran, without its private argument values."""
    logger.info(
        "tool_call",
        extra={"tool": tool_name, "argument_keys": sorted(arguments)},
    )


def log_tool_result(
    logger: logging.Logger,
    tool_name: str,
    success: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record result shape and status, excluding provider error payloads."""
    extra: dict[str, Any] = {"tool": tool_name, "success": success}
    if result:
        extra["result_keys"] = list(result.keys())
    if error:
        extra["error_present"] = True
        logger.error("tool_result", extra=extra)
    else:
        logger.info("tool_result", extra=extra)


def log_error(
    logger: logging.Logger,
    message: str,
    error: Exception | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Retain diagnostic types and context keys without exception payloads."""
    extra: dict[str, Any] = {"diagnostic_chars": len(message)}
    if error:
        extra["error_type"] = type(error).__name__
    if context:
        extra["context_keys"] = sorted(context)
    logger.error("error", extra=extra)
