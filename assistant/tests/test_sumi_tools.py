import json
from subprocess import CompletedProcess, TimeoutExpired
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from assistant.config.loader import TerminalSafetyConfig, ToolConfig
from assistant.tools.groups import filter_tools_payload
from assistant.tools.registry import ToolRegistry
from assistant.tools.sumi_tools import SumiOpenTool, visible_sumi_route


@pytest.fixture
def sumi(monkeypatch):
    calls = []
    instances = {"standalone": None, "preview": {"preview": True}, "sumi": {"preview": False}}
    view = {"screen": "eDP-1", "section": "files", "pinned": True, "visible": True}

    def run(args, **kwargs):
        calls.append(args[1:])
        assert kwargs["timeout"] > 0
        if args[1:] == ["list", "--all", "--json"]:
            return CompletedProcess(args, 0, json.dumps([{"id": key} for key in instances]), "")
        assert args[1:3] == ["ipc", "--id"]
        instance, command, target, method, *parameters = args[3:]
        assert command == "call"
        if (target, method) == ("sumi", "status"):
            status = instances[instance]
            if status is None:
                return CompletedProcess(args, 1, "", "Target not found")
            return CompletedProcess(args, 0, json.dumps(status), "")
        assert instance == "sumi"
        if (target, method) == ("island", "open"):
            assert len(parameters) == 1
            return CompletedProcess(args, 0, "", "")
        assert (target, method) == ("island", "status")
        return CompletedProcess(args, 0, json.dumps([view]), "")

    monkeypatch.setattr("assistant.tools.sumi_tools.shutil.which", lambda _: "/usr/bin/qs")
    monkeypatch.setattr("assistant.tools.sumi_tools.subprocess.run", run)
    return calls, instances, view


def test_opens_and_verifies_a_view_on_the_unique_live_sumi(sumi):
    calls, _, _ = sumi
    result = SumiOpenTool().execute({"route": "files"}, SimpleNamespace(task=None))
    assert result["success"]
    assert result["output"] == {"route": "files", "screen": "eDP-1"}
    assert calls[-2:] == [
        ["ipc", "--id", "sumi", "call", "island", "open", "files"],
        ["ipc", "--id", "sumi", "call", "island", "status"],
    ]
    assert visible_sumi_route() == "files"


@pytest.mark.parametrize("route", ["power", "settings", "island close", "--any-display"])
def test_unknown_routes_never_reach_ipc(sumi, route):
    result = SumiOpenTool().execute({"route": route}, SimpleNamespace(task=None))
    assert result["success"] is False
    assert sumi[0] == []


def test_ambiguous_instances_and_open_dialog_prevent_dispatch(sumi):
    calls, instances, _ = sumi
    instances["other"] = {"preview": False}
    tool = SumiOpenTool()
    assert tool.execute({"route": "bluetooth"}, SimpleNamespace(task=None))["success"] is False
    del instances["other"]
    instances["sumi"]["nativeDialogOpen"] = True
    assert tool.execute({"route": "bluetooth"}, SimpleNamespace(task=None))["success"] is False
    assert all("open" not in call for call in calls)


def test_hidden_matching_route_is_not_verified(sumi):
    calls, _, view = sumi
    view["pinned"] = False
    result = SumiOpenTool().execute({"route": "files"}, SimpleNamespace(task=None))
    assert result["success"] is False
    assert result["delivery"] == "uncertain_delivery"
    assert result["pause"] is True
    assert sum("open" in call for call in calls) == 1
    assert visible_sumi_route() is None


def test_checks_for_stop_before_opening_the_resolved_view(sumi):
    check = Mock(side_effect=InterruptedError)
    context = SimpleNamespace(task=SimpleNamespace(check=check))
    with pytest.raises(InterruptedError):
        SumiOpenTool().execute({"route": "files"}, context)
    check.assert_called_once_with()
    assert all("open" not in call for call in sumi[0])


def test_ipc_timeout_is_an_unavailable_result(monkeypatch):
    def timeout(args, **kwargs):
        raise TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr("assistant.tools.sumi_tools.shutil.which", lambda _: "/usr/bin/qs")
    monkeypatch.setattr("assistant.tools.sumi_tools.subprocess.run", timeout)
    result = SumiOpenTool().execute({"route": "files"}, SimpleNamespace(task=None))
    assert result["success"] is False
    assert result["delivery"] == "not_delivered"


def test_registry_offers_tool_for_sumi_requests():
    registry = ToolRegistry.load_builtin(
        ToolConfig([], [], {}, TerminalSafetyConfig(False, [], [], [], 1))
    )
    assert registry.get("sumi_open").metadata.resource == "desktop"
    payload = registry.as_openai_tools()
    for request in ("open Bluetooth", "show my files", "open Sumi calendar"):
        tools = filter_tools_payload(payload, request)
        assert "sumi_open" in {tool["function"]["name"] for tool in tools}
    chat_tools = filter_tools_payload(payload, "tell me a funny story")
    assert "sumi_open" not in {tool["function"]["name"] for tool in chat_tools}
