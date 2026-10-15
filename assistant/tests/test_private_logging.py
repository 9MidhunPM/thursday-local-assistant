import io
import json
import logging

import pytest

from assistant.logging_utils import (
    PrivateFormatter,
    StructuredFormatter,
    log_error,
    log_model_interaction,
    log_tool_call,
    log_tool_result,
    redact_private_request_text,
)


@pytest.fixture
def recorded():
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.Logger("synthetic-private-log")
    logger.addHandler(Capture())
    return logger, records


def test_user_request_marker_omits_everyday_transcript(recorded):
    logger, records = recorded
    transcript = "My everyday private phrase contains marmalade-peacock."
    logger.info(redact_private_request_text(transcript))
    assert records[0].getMessage() == f"[request content omitted; chars={len(transcript)}]"
    assert "marmalade-peacock" not in repr(records[0].__dict__)


def test_legacy_email_markers_remain_content_free():
    assert redact_private_request_text("Send an email saying private news") == (
        "[email draft request redacted]"
    )
    assert redact_private_request_text("Summarize recent Gmail inbox private news") == (
        "[email inbox request redacted]"
    )


def test_model_records_only_metrics_and_tool_shapes(recorded):
    logger, records = recorded
    request = "spoken-kingfisher-phrase"
    response = "private-papaya-response"
    arguments = {"text": "secret-otter-utterance", "path": "/private/lark-notes"}
    log_model_interaction(
        logger, request, response, [{"name": "speak_text", "arguments": arguments}]
    )
    record = records[0]
    assert record.request_chars == len(request)
    assert record.response_chars == len(response)
    assert record.tool_calls == [{"name": "speak_text", "argument_keys": ["path", "text"]}]
    serialized = StructuredFormatter().format(record)
    payload = json.loads(serialized)
    assert payload["request_chars"] == len(request)
    assert payload["response_chars"] == len(response)
    assert payload["message"] == "model_interaction"
    for private in (request, response, *arguments.values()):
        assert private not in repr(record.__dict__)
        assert private not in serialized


def test_tool_records_argument_names_without_any_argument_values(recorded):
    logger, records = recorded
    log_tool_call(logger, "shell", {"command": "private-toucan", "cwd": "private-badger"})
    record = records[0]
    assert record.tool == "shell"
    assert record.argument_keys == ["command", "cwd"]
    serialized = StructuredFormatter().format(record)
    assert "private-toucan" not in serialized
    assert "private-badger" not in serialized
    assert not hasattr(record, "arguments")


def test_error_helpers_omit_provider_text_but_keep_failure_type_and_result_keys(recorded):
    logger, records = recorded
    log_tool_result(
        logger, "hypruse__desktop", False,
        result={"window": "private-window-title"}, error="private-provider-payload",
    )
    log_error(
        logger, "private-diagnostic", RuntimeError("private-exception-body"),
        context={"request": "private-context-body"},
    )
    result = json.loads(StructuredFormatter().format(records[0]))
    assert result["success"] is False
    assert result["error_present"] is True
    assert result["result_keys"] == ["window"]
    error = json.loads(StructuredFormatter().format(records[1]))
    assert error["error_type"] == "RuntimeError"
    assert error["context_keys"] == ["request"]
    assert error["diagnostic_chars"] == len("private-diagnostic")
    for record in records:
        serialized = StructuredFormatter().format(record)
        for marker in ("private-window", "private-provider", "private-exception", "private-context",
                       "private-diagnostic"):
            assert marker not in serialized
            assert marker not in repr(record.__dict__)


@pytest.mark.parametrize("formatter", [PrivateFormatter(), StructuredFormatter()])
def test_exception_formatter_omits_payload(formatter):
    output = io.StringIO()
    logger = logging.Logger("synthetic-exception")
    handler = logging.StreamHandler(output)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    def provider_failure():
        message = "provider response includes private-oriole-payload"
        raise RuntimeError(message)

    try:
        provider_failure()
    except RuntimeError:
        logger.exception("agent_request_failed")
    assert "RuntimeError" in output.getvalue()
    assert "agent_request_failed" in output.getvalue()
    assert "private-oriole-payload" not in output.getvalue()
