from unittest.mock import Mock

from assistant import server
from assistant.task_attention import TaskAttention, notify_if_offscreen


def test_one_notice_for_offscreen_desktop_run():
    attention = TaskAttention()
    task = {"id": "run", "conversation_id": 4, "status": "completed", "desktop_task": True}

    assert attention.finish(task) == "Desktop task finished."
    assert attention.finish(task) is None


def test_decisions_and_paused_tasks_use_generic_text():
    attention = TaskAttention()
    assert attention.finish({
        "id": "run1", "conversation_id": 4, "status": "completed",
        "desktop_task": True, "needs_decision": True, "detail": "Private task details",
    }) == "Desktop task needs your input."
    assert attention.finish({
        "id": "run2", "conversation_id": 4, "status": "paused", "desktop_task": True,
    }) == "Desktop task paused. Open Thursday to review."
    assert attention.finish({
        "id": "run3", "conversation_id": 4, "status": "cancelled", "desktop_task": True,
    }) is None
    assert attention.finish({
        "id": "run4", "conversation_id": 4, "status": "completed",
    }) is None


def test_only_connected_current_views_suppress_notices():
    stream = server.EventBroadcaster()
    connection = stream.add_client("web", "web-id", view_id="view-id")
    assert not stream.report_task_view("other-view", 1, 4)
    assert stream.report_task_view("view-id", 2, 4)
    assert stream.has_task_view(4)
    assert not stream.has_task_view(5)
    assert stream.report_task_view("view-id", 1, None)
    assert stream.has_task_view(4)
    assert stream.report_task_view("view-id", 3, None)
    assert not stream.has_task_view(4)
    stream.report_task_view("view-id", 4, 4)
    stream.remove_client(connection)
    assert not stream.has_task_view(4)


def test_reconnect_clears_the_superseded_connection_view():
    stream = server.EventBroadcaster()
    old = stream.add_client("web", "web-id", view_id="view-id")
    assert stream.report_task_view("view-id", 1, 4)
    stream.add_client("web", "web-id", view_id="view-id")
    assert not stream.has_task_view(4)
    assert stream.report_task_view("view-id", 1, 4)
    assert not stream.has_task_view(4)
    assert stream.report_task_view("view-id", 2, 5)
    stream.remove_client(old)
    assert stream.has_task_view(5)


def test_notice_checks_native_sumi_and_web_visibility_before_delivery(monkeypatch):
    deliver = Mock()
    is_visible = Mock(side_effect=[True, False, False, False])
    sumi_route = Mock(side_effect=["thursday", "controls"])
    monkeypatch.setattr("assistant.task_attention.visible_sumi_route", sumi_route)
    monkeypatch.setattr("assistant.task_attention._send", deliver)
    for _ in range(3):
        notify_if_offscreen(4, "Desktop task finished.", is_visible, lambda: 4)

    deliver.assert_called_once_with("Desktop task finished.")
