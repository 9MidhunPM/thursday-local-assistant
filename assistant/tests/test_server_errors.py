from __future__ import annotations

import httpx

from assistant.server import _public_agent_error


def test_openai_bad_request_details_are_not_shown_to_user() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    error = httpx.HTTPStatusError(
        "Invalid parameter: messages with role tool must follow tool_calls",
        request=request,
        response=response,
    )

    public = _public_agent_error(error)

    assert public == "Thursday could not complete that model request. Please retry."
    assert "tool_calls" not in public
    assert "api.openai.com" not in public
