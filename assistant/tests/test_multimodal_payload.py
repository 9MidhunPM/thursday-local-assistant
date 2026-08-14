from assistant.llm.client import ChatMessage, OpenAICompatibleClient


def test_chat_completions_payload_preserves_image_content_blocks():
    client = OpenAICompatibleClient(
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-luna",
        temperature=0.2,
        max_tokens=1000,
        timeout_sec=10,
        response_format=None,
        api_key="test-key",
        provider="openai",
    )
    content = [
        {"type": "text", "text": "Review this page"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,YQ==", "detail": "high"},
        },
    ]
    try:
        payload = client._build_payload(
            [ChatMessage(role="user", content=content)],
            tools=None,
            use_response_format=False,
            reasoning_effort="none",
        )
    finally:
        client.close()
    assert payload["messages"][0]["content"] == content
    assert "temperature" not in payload
    assert payload["reasoning_effort"] == "none"
