import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from assistant.agent.tasks import TaskStore
from assistant.integrations.hypruse import HypruseTool


def reply(*messages):
    return {
        "isError": False,
        "content": [{"type": "text", "text": message} for message in messages],
    }


def setup():
    snapshot = {
        "active_window": "0x1",
        "windows": [
            {"address": "0x1", "class": "brave", "pid": 42},
            {"address": "0x2", "class": "editor", "pid": 43, "title": "Notes.md"},
        ],
    }
    bridge = SimpleNamespace(
        snapshot=snapshot,
        snapshot_at=time.monotonic(),
        call=Mock(),
        policy_evidence={"0x1": (time.monotonic(), [{"name": "Preview"}])},
        policy_input=(time.monotonic(), "0x1", "address"),
    )
    return bridge, SimpleNamespace(confirm=Mock(return_value=True))


def test_ambiguous_control_reports_candidates_and_clears_old_click_evidence():
    bridge, context = setup()
    candidates = [{"name": "Preview", "x": 9}, {"name": "Preview", "x": 32}]
    bridge.call.return_value = reply(
        "'Preview' is ambiguous (2 candidates); call again with index=N (0-based) "
        "or a more specific name:",
        json.dumps(candidates),
    )
    result = HypruseTool(bridge, "click_ui").execute({"window": "0x1", "name": "Preview"}, context)
    assert not result["success"]
    assert result["error_code"] == "ambiguous_control"
    assert result["delivery"] == "not_delivered"
    assert result["output"] == candidates
    assert result["blocks_following_actions"]
    assert not bridge.policy_evidence
    assert bridge.policy_input is None


@pytest.mark.parametrize(
    "message",
    [
        "timeout: no matching openwindow event within 2s",
        "event socket unavailable: connection refused",
    ],
)
def test_failed_wait_does_not_send_dependent_input(message):
    bridge, context = setup()
    bridge.call.return_value = reply(message)
    result = HypruseTool(bridge, "sequence").execute(
        {
            "steps": [
                {"op": "wait_for", "event": "openwindow", "timeout_s": 2},
                {"op": "keyboard", "window": "0x1", "action": "type", "text": "wrong"},
            ]
        },
        context,
    )
    assert not result["success"]
    assert result["delivery"] == "not_delivered"
    assert result["completed_steps"] == 0
    assert [call.args[0] for call in bridge.call.call_args_list] == ["wait_for"]


def test_interrupted_native_batch_counts_only_delivered_prefix_and_never_presses_enter():
    bridge, context = setup()

    def call(operation, _args):
        if operation == "desktop":
            return reply(json.dumps(bridge.snapshot))
        return reply(
            "sequence: stopped after 2/2 steps, step 2 raised: the seat moved\n"
            "[1] keyboard key: pressed ctrl+l into 0x1\n"
            "[2] keyboard: ERROR the seat moved",
            json.dumps(bridge.snapshot),
        )

    bridge.call.side_effect = call
    result = HypruseTool(bridge, "sequence").execute(
        {
            "steps": [
                {"op": "keyboard", "window": "0x1", "action": "key", "keys": "ctrl+l"},
                {
                    "op": "keyboard",
                    "window": "0x1",
                    "action": "type",
                    "text": "https://example.test",
                },
                {"op": "keyboard", "window": "0x1", "action": "key", "keys": "enter"},
            ]
        },
        context,
    )
    assert not result["success"]
    assert result["error_code"] == "sequence_interrupted"
    assert result["completed_steps"] == 1
    assert result["delivery"] == "partial_delivery"
    assert result["pause"]
    assert [call.args[0] for call in bridge.call.call_args_list] == ["desktop", "sequence"]
    assert bridge.policy_input is None


@pytest.mark.parametrize(
    "change", ["closed", "reused", "title changed", "focus refused", "focus changed"]
)
def test_keyboard_revalidates_target_before_dispatch(change):
    bridge, context = setup()
    fresh_windows = bridge.snapshot["windows"]
    if change == "closed":
        fresh_windows = fresh_windows[:1]
    elif change == "reused":
        fresh_windows = [fresh_windows[0], {**fresh_windows[1], "pid": 99}]
    elif change == "title changed":
        fresh_windows = [fresh_windows[0], {**fresh_windows[1], "title": "Other.md"}]
    fresh = {**bridge.snapshot, "windows": fresh_windows}
    if change == "focus changed":
        fresh["active_window"] = "0x2"

    def call(operation, _args):
        if operation == "hypr":
            return reply("focused 0x2", json.dumps(fresh))
        return reply(json.dumps(fresh))

    bridge.call.side_effect = call
    result = HypruseTool(bridge, "keyboard").execute(
        {"window": "0x2", "action": "type", "text": "hello"},
        context,
    )
    assert not result["success"]
    assert result["error_code"] == "target_changed"
    assert [call.args[0] for call in bridge.call.call_args_list] == (
        ["desktop", "hypr"] if change == "focus refused" else ["desktop"]
    )


@pytest.mark.parametrize("intervening_action", [False, True])
def test_sequence_keeps_only_fresh_capture_outside_checkpoint_evidence(intervening_action):
    bridge, context = setup()
    encoded = base64.b64encode(b"fixture image").decode()

    def call(operation, _args):
        if operation == "screenshot":
            return {
                "content": [
                    {"type": "image", "data": encoded, "mimeType": "image/png"},
                    {"type": "text", "text": '{"target":"0x1"}'},
                ]
            }
        return reply("move ok; cursor now at (0, 0)", json.dumps(bridge.snapshot))

    bridge.call.side_effect = call
    steps = [{"op": "screenshot", "window": "0x1"}]
    if intervening_action:
        steps.append({"op": "pointer", "action": "move", "x": 0, "y": 0})
    result = HypruseTool(bridge, "sequence").execute({"steps": steps}, context)
    assert result["success"]
    assert bool(result["_images"]) is not intervening_action
    assert bool(result["preview"]) is not intervening_action
    assert encoded not in str(result["output"])


def test_delivered_sequence_prefix_is_saved_before_stop(tmp_path):
    bridge, context = setup()
    store = TaskStore(tmp_path / "tasks.db")
    context.task = store.create("Move then type", 1)
    original = context.task.data["id"]

    def call(operation, _args):
        assert operation == "pointer"
        context.task.cancelled.set()
        return reply("move ok; cursor now at (0, 0)", json.dumps(bridge.snapshot))

    bridge.call.side_effect = call
    steps = [
        {"op": "pointer", "action": "move", "x": 0, "y": 0},
        {"op": "keyboard", "action": "key", "keys": "ctrl+w", "window": "0x1"},
    ]
    with pytest.raises(InterruptedError):
        HypruseTool(bridge, "sequence").execute({"steps": steps}, context)
    checkpoint = store.get(original)["outcomes"]
    assert len(checkpoint) == 1
    assert checkpoint[0]["tool"] == "hypruse__pointer"
    assert checkpoint[0]["completed_steps"] == 1
    assert checkpoint[0]["delivery"] == "delivered"
    assert checkpoint[0]["sequence_child"]


def test_ambiguous_click_after_delivered_action_requires_reobservation():
    bridge, context = setup()

    def call(operation, _args):
        if operation == "pointer":
            return reply("move ok; cursor now at (0, 0)", json.dumps(bridge.snapshot))
        assert operation == "click_ui"
        return reply(
            "'Preview' is ambiguous (2 candidates); call again with index=N (0-based):",
            '[{"name": "Preview"}, {"name": "Preview"}]',
        )

    bridge.call.side_effect = call
    steps = [
        {"op": "pointer", "action": "move", "x": 0, "y": 0},
        {"op": "click_ui", "name": "Preview", "window": "0x1"},
        {"op": "keyboard", "action": "type", "text": "wrong", "window": "0x1"},
    ]
    result = HypruseTool(bridge, "sequence").execute({"steps": steps}, context)
    assert not result["success"]
    assert result["completed_steps"] == 1
    assert result["delivery"] == "partial_delivery"
    assert result["pause"]
    assert bridge.snapshot is None
    assert not bridge.policy_evidence


def test_local_keyboard_peek_preserves_seat_guard_and_detects_pointer_takeover():
    bridge, context = setup()
    bridge.snapshot["cursor"] = [10, 20]
    bridge.peek_snapshot = Mock(return_value={**bridge.snapshot, "cursor": [20, 30]})
    result = HypruseTool(bridge, "keyboard").execute(
        {"window": "0x2", "action": "type", "text": "hello"},
        context,
    )
    assert result["error_code"] == "target_changed"
    assert result["delivery"] == "not_delivered"
    bridge.call.assert_not_called()


def test_click_accessibility_result_refreshes_focus_for_followup_keyboard():
    bridge, context = setup()
    post_click = {**bridge.snapshot, "active_window": "0x2"}
    bridge.peek_snapshot = Mock(return_value=post_click)
    bridge.call.return_value = reply("clicked editor 'Notes' at (9, 22)", '[{"name":"Notes"}]')
    click = HypruseTool(bridge, "click_ui").execute({"window": "0x2", "name": "Notes"}, context)
    assert click["output"] == [{"name": "Notes"}]
    assert bridge.snapshot["active_window"] == "0x2"
    bridge.call.return_value = reply("typed 5 characters into 0x2", '[{"value":"hello"}]')
    typed = HypruseTool(bridge, "keyboard").execute(
        {"window": "0x2", "action": "type", "text": "hello"},
        context,
    )
    assert typed["success"]
    assert [call.args[0] for call in bridge.call.call_args_list] == ["click_ui", "keyboard"]
