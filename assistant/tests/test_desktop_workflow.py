from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from assistant.agent.desktop_context import desktop_request
from assistant.integrations.hypruse import HypruseTool
from assistant.llm.client import InferenceStats, LlmResponse, ToolCall
from assistant.tests.test_copilot import make_agent
from assistant.tools.base import BaseTool, ToolMetadata


def call(tool, arguments):
    return LlmResponse(None, [ToolCall(tool, tool, arguments)], {}, InferenceStats())


def reply(text):
    return LlmResponse(text, [], {}, InferenceStats())


class FixtureTool(BaseTool):
    def __init__(self, name, response):
        self.metadata = ToolMetadata(name, "Fixture", {"type": "object"})
        self.run = Mock(return_value=response)

    def execute(self, arguments, context):
        return self.run(arguments, context)


def setup_desktop(agent):
    windows = [
        {"address": "0x1", "class": "brave", "pid": 1, "workspace": 1, "title": "Issue"},
        {"address": "0x2", "class": "editor", "pid": 2, "workspace": 1, "title": "Project notes"},
        {"address": "0x3", "class": "editor", "pid": 3, "workspace": 2, "title": "Other workspace"},
        {"address": "0x4", "class": "editor", "pid": 4, "workspace": 1, "hidden": True},
    ]
    bridge = SimpleNamespace(snapshot=None, snapshot_at=0, close=Mock())
    bridge.call = Mock(return_value={
        "structuredContent": {
            "windows": windows, "active_window": "0x1",
            "monitors": [{"name": "virtual", "focused": True, "active_workspace": 1}],
        },
        "content": [],
    })
    agent.hypruse = bridge
    agent._tool_registry.register(HypruseTool(bridge))
    return bridge, windows


def model_context(agent, ordinal):
    messages = agent._llm.chat.call_args_list[ordinal].args[0]
    return next(message.content for message in messages
                if str(message.content).startswith("Desktop and file references"))


def test_workspace_and_ordered_file_matches_survive_follow_up(tmp_path):
    agent, _ = make_agent(tmp_path, [
        call("hypruse__ui", {"window": "0x1"}),
        call("find_file_system", {"query": "project notes"}),
        reply("I found two matches."),
        call("open_path", {"path": "/tmp/b/project-notes.md"}),
        reply("Opened the second match."),
    ])
    bridge, windows = setup_desktop(agent)
    agent._tool_registry.register(FixtureTool("hypruse__ui", {"success": True}))
    agent._tool_registry.register(FixtureTool("find_file_system", {
        "success": True,
        "output": {"results": [
            {"index": 1, "type": "file", "path": "/tmp/a/project-notes.md"},
            {"index": 2, "type": "file", "path": "/tmp/b/project-notes.md"},
        ]},
    }))
    open_path = FixtureTool("open_path", {"success": True})
    agent._tool_registry.register(open_path)
    result = agent.handle_message("Read this Brave tab and find the project notes.")
    assert result == "I found two matches."
    assert "Other workspace" not in model_context(agent, 0)
    assert "0x4" not in model_context(agent, 0)
    assert agent.tasks.latest()["desktop_references"]["windows"][0]["address"] == "0x1"
    windows.pop(0)  # the same address has been reused for a different process
    windows.append({"address": "0x1", "class": "brave", "pid": 5, "workspace": 2})
    agent.handle_message("Open the other match")
    context = model_context(agent, 3)
    assert context.index("/tmp/a/project-notes.md") < context.index("/tmp/b/project-notes.md")
    assert '"references": {"windows": []' in context
    assert open_path.run.call_args.args[0]["path"] == "/tmp/b/project-notes.md"
    assert [item.args[0] for item in bridge.call.call_args_list] == ["desktop", "desktop"]
    agent.close()


@pytest.mark.parametrize("prompt", ["Hello", "What is a workspace?", "How do I use Brave?"])
def test_ordinary_chat_never_observes_desktop(tmp_path, prompt):
    agent, _ = make_agent(tmp_path, [reply("Hello")])
    bridge, _ = setup_desktop(agent)
    assert agent.handle_message(prompt) == "Hello"
    bridge.call.assert_not_called()
    agent.close()


@pytest.mark.parametrize("prompt", ["What is on my screen?", "Whats on my screen?", "Show my files"])
def test_desktop_requests_include_common_voice_phrasings(prompt):
    assert desktop_request(prompt)


def test_numbered_file_follow_up_keeps_selected_path(tmp_path):
    agent, _ = make_agent(tmp_path, [
        call("search_files", {"query": "project notes"}),
        reply("I found two matches. Which one should I open?"),
        call("open_path", {"path": "/tmp/b.md"}),
        reply("Opened the second match."),
    ])
    agent._tool_registry.register(FixtureTool("search_files", {
        "success": True, "output": ["/tmp/a.md", "/tmp/b.md"],
    }))
    agent._tool_registry.register(FixtureTool("open_path", {"success": True}))
    agent.handle_message("Find project notes")
    agent.handle_message("2")
    assert '"index": 2, "type": "file", "path": "/tmp/b.md"' in model_context(agent, 2)
    assert agent.tasks.latest()["desktop_references"]["selected_file"] == "/tmp/b.md"
    agent.close()


def test_resume_observes_workspace_and_preserves_previous_deliveries(tmp_path):
    agent, _ = make_agent(tmp_path, [reply("The original action already ran.")])
    bridge, _ = setup_desktop(agent)
    parent = agent.tasks.create("Use this Brave tab", None)
    previous = {"tool": "hypruse__keyboard", "success": True, "delivery": "delivered"}
    parent.data["outcomes"].append(previous)
    parent.update(desktop_task=True, desktop_references={"selected_file": "/tmp/project.md"})
    parent.finish("paused", "Review")
    resumed = agent.tasks.create(parent.data["goal"], None, parent=parent.data["id"])
    resumed.data["outcomes"] = list(parent.data["outcomes"])
    agent.handle_message("Resume this task: Use this Brave tab", task=resumed)
    bridge.call.assert_called_once_with("desktop", {})
    messages = agent._llm.chat.call_args.args[0]
    assert any('"delivery": "delivered"' in str(message.content) for message in messages)
    assert "/tmp/project.md" in model_context(agent, 0)
    agent.close()


def test_failed_desktop_step_stops_queued_input_and_preserves_tool_results(tmp_path):
    action = LlmResponse(None, [
        ToolCall("focus", "window_management", {"action": "focus", "window_name": "editor"}),
        ToolCall("type", "hypruse__keyboard", {"action": "type", "window": "0x2", "text": "hello"}),
    ], {}, InferenceStats())
    agent, _ = make_agent(tmp_path, [action, reply("Which editor did you mean?")])
    setup_desktop(agent)
    agent._tool_registry.register(FixtureTool("window_management", {
        "success": False, "error_code": "ambiguous_window", "error": "Multiple matches",
    }))
    keyboard = FixtureTool("hypruse__keyboard", {"success": True})
    agent._tool_registry.register(keyboard)
    assert agent.handle_message("Focus the editor") == "Which editor did you mean?"
    keyboard.run.assert_not_called()
    assert agent.tasks.latest()["needs_decision"]
    assert [message.tool_call_id for message in agent._short_term.as_list()
            if message.role == "tool"] == ["focus", "type"]
    agent.close()


def test_verified_window_focus_refreshes_observation_before_typing(tmp_path):
    action = LlmResponse(None, [
        ToolCall("focus", "window_management", {"action": "focus", "window_name": "editor"}),
        ToolCall("type", "hypruse__keyboard", {"action": "type", "window": "0x2", "text": "hello"}),
    ], {}, InferenceStats())
    agent, _ = make_agent(tmp_path, [action, reply("Done.")])
    bridge, windows = setup_desktop(agent)
    focus = FixtureTool("window_management", {"success": True})

    def activate(_arguments, _context):
        bridge.call.return_value["structuredContent"]["active_window"] = "0x2"
        return {"success": True, "window": windows[1], "backend": "hyprland"}

    focus.run.side_effect = activate
    keyboard = FixtureTool("hypruse__keyboard", {"success": True})
    agent._tool_registry.register(focus)
    agent._tool_registry.register(keyboard)
    agent.handle_message("Focus the editor and type hello.")
    assert bridge.call.call_count == 2
    assert bridge.snapshot["active_window"] == "0x2"
    keyboard.run.assert_called_once()
    agent.close()


def test_text_result_after_keyboard_retires_capture(tmp_path):
    agent, _ = make_agent(tmp_path, [
        call("hypruse__screenshot", {"window": "0x2"}),
        call("hypruse__keyboard", {"action": "type", "window": "0x2", "text": "hello"}),
        reply("Done"),
    ])
    setup_desktop(agent)
    agent._tool_registry.register(FixtureTool("hypruse__screenshot", {
        "success": True, "_images": [{"type": "image_url", "image_url": {"url": "fixture"}}],
        "preview": "/api/captures/fixture",
    }))
    agent._tool_registry.register(FixtureTool("hypruse__keyboard", {"success": True}))
    agent.handle_message("Type hello in the editor.")
    prompts = [call.args[0] for call in agent._llm.chat.call_args_list]
    assert any(isinstance(message.content, list) for message in prompts[1])
    assert not any(isinstance(message.content, list) for message in prompts[2])
    assert agent.tasks.latest()["preview"] is None
    agent.close()
