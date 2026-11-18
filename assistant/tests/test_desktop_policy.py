import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from assistant.agent.tasks import TaskStore
from assistant.integrations.desktop_policy import needs_approval
from assistant.integrations.hypruse import HypruseTool


def bridge():
    return SimpleNamespace(
        snapshot={
            "active_window": "0x1",
            "windows": [{"address": "0x1", "class": "brave"}],
            "cursor": [10, 10],
        },
        snapshot_at=time.monotonic(),
        policy_evidence={},
    )


@pytest.mark.parametrize(
    ("op", "args", "expected"),
    [
        ("pointer", {"action": "move"}, False),
        ("pointer", {"action": "scroll"}, False),
        ("pointer", {"action": "click", "x": 10, "y": 10}, True),
        ("click_ui", {"name": "Search"}, False),
        ("click_ui", {"name": "Close tab"}, True),
        ("click_ui", {"name": "Send message"}, True),
        ("click_ui", {"name": "Save"}, True),
        ("click_ui", {"name": "Save As…"}, True),
        ("click_ui", {"name": "Saved items"}, False),
        ("click_ui", {"name": "Change password"}, True),
        ("keyboard", {"action": "key", "keys": "Ctrl+Shift+W"}, True),
        ("keyboard", {"action": "key", "keys": "Alt+F4"}, True),
        ("keyboard", {"action": "key", "keys": "Ctrl+L"}, False),
        ("keyboard", {"action": "type", "text": "hello"}, False),
    ],
)
def test_effect_policy(op, args, expected):
    assert bool(needs_approval(op, args, bridge())) is expected


def test_pixel_and_mark_use_fresh_observed_effect():
    b = bridge()
    b.policy_evidence["0x1"] = (
        time.monotonic(),
        {"elements": [{"name": "Search", "mark": 1, "x": 10, "y": 10}]},
    )
    assert not needs_approval("pointer", {"action": "click", "x": 10, "y": 10}, b)
    assert not needs_approval("click_ui", {"mark": 1}, b)
    b.policy_evidence["0x1"][1]["elements"][0]["name"] = "Close"
    assert needs_approval("pointer", {"action": "click", "x": 10, "y": 10}, b)
    assert needs_approval("click_ui", {"mark": 1}, b)
    b.policy_evidence["0x1"] = (0, {"elements": [{"name": "Search", "mark": 1}]})
    assert needs_approval("click_ui", {"mark": 1}, b)


def test_sequence_executes_safe_prefix_before_gate_and_decline_stops():
    b = bridge()
    b.call = Mock(return_value={"structuredContent": {"done": True}})

    def decline(_):
        assert [c.args[0] for c in b.call.call_args_list] == ["pointer"]
        return False

    context = SimpleNamespace(confirm=Mock(side_effect=decline))
    with pytest.raises(PermissionError):
        HypruseTool(b, "sequence").execute(
            {
                "steps": [
                    {"op": "pointer", "action": "move", "x": 4, "y": 6},
                    {"op": "keyboard", "action": "key", "keys": "ctrl+w", "window": "0x1"},
                    {"op": "pointer", "action": "move", "x": 8, "y": 9},
                ]
            },
            context,
        )
    context.confirm.assert_called_once()


def test_activity_is_bounded_correlated_and_excludes_private_arguments(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    updates = []
    store.on_update = updates.append
    task = store.create("test", 1)
    for _ in range(52):
        action = task.action_start(
            "hypruse__keyboard", {"action": "type", "window": "0x1", "text": "SECRET WORDS"}
        )
        task.action_finish(action, "completed", "Result received")
    assert len(task.data["activities"]) == 50
    assert len({a["id"] for a in task.data["activities"]}) == 50
    assert task.data["activity"]["status"] == "completed"
    assert updates[-1]["activity"]["duration_ms"] >= 0
    assert "SECRET WORDS" not in str(task.data["activities"])
    assert updates[1]["activity"]["status"] == "running"


def test_safe_address_input_batches_before_navigation():
    b = bridge()
    b.call = Mock(side_effect=lambda operation, _: {
        "structuredContent": b.snapshot if operation == "desktop" else {"delivered": True}
    })
    context = SimpleNamespace(confirm=Mock(return_value=True))
    result = HypruseTool(b, "sequence").execute(
        {
            "steps": [
                {"op": "keyboard", "action": "key", "keys": "ctrl+l", "window": "0x1"},
                {
                    "op": "keyboard",
                    "action": "type",
                    "text": "https://example.com",
                    "window": "0x1",
                },
                {"op": "keyboard", "action": "key", "keys": "enter", "window": "0x1"},
            ]
        },
        context,
    )
    assert result["success"]
    assert [c.args[0] for c in b.call.call_args_list] == ["desktop", "sequence", "desktop", "keyboard"]
    context.confirm.assert_not_called()


def test_text_only_ui_result_can_ground_pixel_click():
    b = bridge()
    b.call = Mock(
        return_value={"content": [{"type": "text", "text": '[{"name":"Search", "x":10, "y":10}]'}]}
    )
    context = SimpleNamespace(confirm=Mock(return_value=True))
    HypruseTool(b, "ui").execute({"window": "0x1"}, context)
    HypruseTool(b, "pointer").execute({"action": "click", "x": 10, "y": 10}, context)
    context.confirm.assert_not_called()


@pytest.mark.parametrize("combo", ["SUPER+X", "win+x", "MOD4 x"])
def test_keyboard_cannot_bypass_consequential_configured_binding(combo):
    b = bridge()
    b.policy_bindings = (time.monotonic(), [{"combo": "SUPER+X", "action": "killactive"}])
    assert needs_approval("use_bind", {"combo": combo}, b)
    assert needs_approval("keyboard", {"action": "key", "keys": combo}, b)


def test_duplicate_and_stale_bindings_do_not_override_consequential_effects():
    b = bridge()
    entries = [
        {"combo": "SUPER+X", "action": "workspace"},
        {"combo": "SUPER+X", "action": "killactive"},
    ]
    b.policy_bindings = (time.monotonic(), entries)
    assert needs_approval("keyboard", {"action": "key", "keys": "super+x"}, b)
    assert needs_approval("use_bind", {"combo": "super+x"}, b)
    b.policy_bindings = (0, entries[:1])
    assert needs_approval("keyboard", {"action": "key", "keys": "super+x"}, b)


def test_safe_binding_does_not_override_hardcoded_closure_gate():
    b = bridge()
    b.policy_bindings = (time.monotonic(), [{"combo": "CTRL+W", "action": "workspace"}])
    assert needs_approval("keyboard", {"action": "key", "keys": "ctrl+w"}, b)
    b.policy_bindings = (time.monotonic(), [{"combo": "SUPER+1", "action": "workspace"}])
    assert not needs_approval("keyboard", {"action": "key", "keys": "super+1"}, b)
    assert not needs_approval("use_bind", {"combo": "super+1"}, b)
