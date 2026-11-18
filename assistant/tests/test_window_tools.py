import json
from subprocess import CompletedProcess

from assistant.tools import window_tools


def window(address, workspace, title="Project Notes"):
    return {
        "address": address,
        "mapped": True,
        "title": title,
        "class": "editor",
        "pid": int(address, 16),
        "workspace": {"id": workspace, "name": str(workspace)},
    }


def hyprland(monkeypatch, windows, workspace, active):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[1] == "clients":
            output = windows
        elif command[1] == "activeworkspace":
            output = {"id": workspace}
        elif command[1] == "activewindow":
            output = active
        else:
            return CompletedProcess(command, 0, "ok", "")
        return CompletedProcess(command, 0, json.dumps(output), "")

    monkeypatch.setattr(window_tools.shutil, "which", lambda command: command)
    monkeypatch.setattr(window_tools.subprocess, "run", run)
    return calls


def focus(name):
    return window_tools.WindowManagementTool().execute(
        {"action": "focus", "window_name": name}, None
    )


def test_focus_prefers_unique_match_on_active_workspace(monkeypatch):
    first, current = window("0x1", 1), window("0x2", 2)
    calls = hyprland(monkeypatch, [first, current], 2, current)

    result = focus("notes")

    assert result["success"]
    assert result["window"]["address"] == "0x2"
    assert result["window"]["pid"] == current["pid"]
    assert ["hyprctl", "dispatch", "focuswindow", "address:0x2"] in calls
    assert calls[-1] == ["hyprctl", "activewindow", "-j"]


def test_ambiguous_window_titles_never_dispatch_focus(monkeypatch):
    first, second, elsewhere = window("0x1", 1), window("0x2", 1), window("0x3", 2)
    calls = hyprland(monkeypatch, [first, second, elsewhere], 1, first)

    result = focus("Project Notes")

    assert result["success"] is False
    assert result["error_code"] == "ambiguous_window"
    assert [candidate["address"] for candidate in result["candidates"]] == ["0x1", "0x2", "0x3"]
    assert all(command[1] != "dispatch" for command in calls)


def test_explicit_address_can_focus_across_workspaces(monkeypatch):
    first, second = window("0x1", 1), window("0x2", 2)
    calls = hyprland(monkeypatch, [first, second], 1, second)

    assert focus("0x2")["success"]
    assert ["hyprctl", "dispatch", "focuswindow", "address:0x2"] in calls
    assert not any(command[1] == "activeworkspace" for command in calls)


def test_dispatch_success_does_not_verify_wrong_window(monkeypatch):
    intended, other = window("0x1", 1), window("0x2", 1)
    calls = hyprland(monkeypatch, [intended, other], 1, other)
    monkeypatch.setattr(window_tools.time, "sleep", lambda _seconds: None)

    result = focus("0x1")

    assert result["success"] is False
    assert [command[0] for command in calls].count("wmctrl") == 0
    assert sum(command[1] == "dispatch" for command in calls) == 1


def test_focus_rejects_reused_address_for_a_different_process(monkeypatch):
    intended = window("0x1", 1)
    replacement = {**intended, "pid": 222}
    hyprland(monkeypatch, [intended], 1, replacement)
    monkeypatch.setattr(window_tools.time, "sleep", lambda _seconds: None)

    assert focus("0x1")["success"] is False


def test_focus_fails_when_target_disappears_before_verification(monkeypatch):
    calls = hyprland(monkeypatch, [window("0x1", 1)], 1, {})
    monkeypatch.setattr(window_tools.time, "sleep", lambda _seconds: None)

    assert focus("0x1")["success"] is False
    assert sum(command[1] == "dispatch" for command in calls) == 1


def test_list_exposes_addresses_and_workspace_for_disambiguation(monkeypatch):
    hyprland(monkeypatch, [window("0x1", 1)], 1, {})

    result = window_tools.WindowManagementTool().execute({"action": "list"}, None)

    assert result["output"] == "Open windows:\neditor - Project Notes"
    assert result["windows"] == [
        {"address": "0x1", "title": "Project Notes", "class": "editor", "workspace": 1, "pid": 1}
    ]


def test_x11_focus_uses_window_id_and_checks_active_window(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        output = {
            ("wmctrl", "-lp"): "0x0010  0  112 workstation Project Notes\n",
            ("wmctrl", "-ia", "0x0010"): "",
            ("xprop", "-root", "_NET_ACTIVE_WINDOW"): "_NET_ACTIVE_WINDOW: window id # 0x10",
        }
        return CompletedProcess(command, 0, output[tuple(command)], "")

    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setattr(
        window_tools.shutil, "which", lambda command: command if command != "hyprctl" else None
    )
    monkeypatch.setattr(window_tools.subprocess, "run", run)

    assert focus("Project Notes")["success"]
    assert calls[-2:] == [["wmctrl", "-ia", "0x0010"], ["xprop", "-root", "_NET_ACTIVE_WINDOW"]]
