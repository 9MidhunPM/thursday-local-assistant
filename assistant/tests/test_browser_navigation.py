from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from assistant.llm.client import InferenceStats, LlmResponse, ToolCall
from assistant.tests.test_copilot import make_agent
from assistant.tools.browser_navigation import OpenUrlTool
from assistant.tools.browser_tools import WebSearchTool
from assistant.tools.groups import filter_tools_payload
from assistant.tools.youtube_search_tool import YouTubeSearchTool


def context():
    return SimpleNamespace(task=SimpleNamespace())


def search_result():
    return {
        "success": True,
        "results": [
            {
                "title": "Example creator",
                "url": "https://www.youtube.com/@example",
                "snippet": "Official channel",
            },
            {
                "title": "Video",
                "url": "https://www.youtube.com/watch?v=example",
                "snippet": "A video",
            },
            {
                "title": "Other",
                "url": "https://youtube.com.evil.invalid/@example",
                "snippet": "Unrelated",
            },
        ],
    }


def test_query_variants_never_launch_tabs():
    ctx = context()
    with (
        patch.object(WebSearchTool, "execute", return_value=search_result()) as search,
        patch("webbrowser.open") as opened,
    ):
        tool = YouTubeSearchTool()
        results = [
            tool.execute({"query": query, "kind": "channel"}, ctx)
            for query in [
                "Example creator official channel",
                "Example creator official channel",
                "Example creator",
                "Example creator",
                "Example creator official YouTube channel",
            ]
        ]
    opened.assert_not_called()
    assert search.call_count == 3  # Exact repeated searches reuse their evidence.
    assert all(r["results"] == [search_result()["results"][0]] for r in results)
    assert all(r["browser_opened"] is False for r in results)


def test_dispatch_once_per_task_across_tool_instances():
    ctx = context()
    with patch("webbrowser.open", return_value=True) as opened:
        first = OpenUrlTool().execute({"url": "https://example.com"}, ctx)
        second = OpenUrlTool().execute({"url": "https://example.com/"}, ctx)
        OpenUrlTool().execute({"url": "https://example.com/"}, context())
    assert opened.call_count == 2
    assert first["delivery"] == "dispatched"
    assert first["page_verified"] is False
    assert second["already_dispatched"] is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:secret@example.com/",
        "https://example.com/\nInjected",
        "example.com",
        "https://",
    ],
)
def test_invalid_urls_do_not_launch_or_echo_input(url):
    with patch("webbrowser.open") as opened:
        result = OpenUrlTool().execute({"url": url}, context())
    assert result["success"] is False
    assert url not in result["error"]
    opened.assert_not_called()


@pytest.mark.parametrize(
    ("outcome", "delivery"),
    [(False, "not_delivered"), (RuntimeError("private detail"), "uncertain_delivery")],
)
def test_failed_dispatch_is_truthful_and_not_retried(outcome, delivery):
    opener = (
        Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
    )
    ctx = context()
    with patch("webbrowser.open", opener):
        first = OpenUrlTool().execute({"url": "https://example.com/"}, ctx)
        second = OpenUrlTool().execute({"url": "https://example.com/"}, ctx)
    opener.assert_called_once()
    assert first["success"] is False
    assert first["delivery"] == delivery
    assert second["delivery"] == delivery
    assert "private detail" not in str(first)


def test_channel_navigation_tools_available_without_discovery():
    tools = [
        tool.as_openai_tool() for tool in [OpenUrlTool(), YouTubeSearchTool(), WebSearchTool()]
    ]
    selected = filter_tools_payload(tools, "open Example creator's channel")
    assert {t["function"]["name"] for t in selected} == {"open_url", "youtube_search", "web_search"}


def test_agent_replay_search_variants_then_one_channel_dispatch(tmp_path):
    calls = [
        ToolCall(str(i), "youtube_search", {"query": query, "kind": "channel"})
        for i, query in enumerate(
            [
                "Example creator official channel",
                "Example creator official channel",
                "Example creator",
                "Example creator",
                "Example creator official YouTube channel",
            ]
        )
    ]
    calls += [
        ToolCall("open1", "open_url", {"url": "https://www.youtube.com/@example"}),
        ToolCall("open2", "open_url", {"url": "https://www.youtube.com/@example"}),
    ]
    responses = [
        LlmResponse(None, calls, {}, InferenceStats()),
        LlmResponse("Sent the channel to your browser.", [], {}, InferenceStats()),
    ]
    agent, _ = make_agent(tmp_path, responses)
    for tool in [YouTubeSearchTool(), OpenUrlTool()]:
        agent._tool_registry.register(tool)
    with (
        patch.object(WebSearchTool, "execute", return_value=search_result()),
        patch("webbrowser.open", return_value=True) as opened,
    ):
        answer = agent.handle_message("Open Example creator's channel")
    opened.assert_called_once_with("https://www.youtube.com/@example", new=2)
    assert answer == "Sent the channel to your browser."
    assert agent.tasks.latest()["status"] == "completed"
    agent.close()


def test_resolved_channel_takes_one_search_and_one_navigation(tmp_path):
    responses = [
        LlmResponse(
            None,
            [
                ToolCall(
                    "find",
                    "youtube_search",
                    {
                        "query": "Example creator",
                        "kind": "channel",
                    },
                )
            ],
            {},
            InferenceStats(),
        ),
        LlmResponse(
            None,
            [
                ToolCall(
                    "open",
                    "open_url",
                    {
                        "url": "https://www.youtube.com/@example",
                    },
                )
            ],
            {},
            InferenceStats(),
        ),
        LlmResponse("Sent the channel to your browser.", [], {}, InferenceStats()),
    ]
    agent, _ = make_agent(tmp_path, responses)
    for tool in [YouTubeSearchTool(), OpenUrlTool()]:
        agent._tool_registry.register(tool)
    with (
        patch.object(WebSearchTool, "execute", return_value=search_result()) as search,
        patch("webbrowser.open", return_value=True) as opened,
    ):
        agent.handle_message("Open Example creator's channel")
    search.assert_called_once()
    opened.assert_called_once()
    assert [item["tool"] for item in agent.tasks.latest()["outcomes"]] == [
        "youtube_search",
        "open_url",
    ]
    first_tools = agent._llm.chat.call_args_list[0].kwargs["tools"]
    assert {"open_url", "youtube_search"} <= {item["function"]["name"] for item in first_tools}
    agent.close()
