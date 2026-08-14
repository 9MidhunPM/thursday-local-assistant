from __future__ import annotations

from types import SimpleNamespace

from assistant.tools.gmail_tool import GmailInboxSummaryTool, _clean_message_body


class FakeAutomation:
    def __init__(self, inbox):
        self.inbox = inbox

    def run(self, _operation, timeout=90):
        _ = timeout
        return self.inbox


class FakeBridge:
    def __init__(self, inbox):
        self.inbox = inbox
        self.calls = []

    def request(self, action, payload, timeout=90):
        self.calls.append((action, payload, timeout))
        return self.inbox


class FakeConnectedBridge(FakeBridge):
    def status(self):
        return {"connected": True, "pending": 0}


class FakeController:
    def __init__(self):
        self.calls = []

    def open_url(self, url, title_hint=None):
        self.calls.append((url, title_hint))
        return True, None


def test_clean_message_body_removes_quoted_history() -> None:
    cleaned = _clean_message_body(
        "Please send the report by Friday.\n\nOn Thu, 13 Aug 2026, Alex wrote:\n> old text"
    )
    assert cleaned == "Please send the report by Friday."


def test_inbox_summary_batches_twenty_messages_without_returning_bodies() -> None:
    messages = [
        {
            "number": str(index),
            "sender": f"Person {index}",
            "subject": f"Subject {index}",
            "date": "Aug 14",
            "body": f"Private body {index}",
        }
        for index in range(1, 21)
    ]
    prompts: list[str] = []

    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return "Final detailed summary" if len(prompts) == 5 else "Batch summary"

    tool = GmailInboxSummaryTool(
        automation=FakeAutomation(  # type: ignore[arg-type]
            {"login_required": False, "messages": messages, "warnings": []}
        )
    )
    result = tool.execute({}, SimpleNamespace(summarize_private_text=summarize))
    assert result["success"]
    assert result["count"] == 20
    assert result["summary"] == "Final detailed summary"
    assert len(prompts) == 5
    assert "Private body" not in repr(result)


def test_inbox_summary_surfaces_one_time_login() -> None:
    tool = GmailInboxSummaryTool(
        automation=FakeAutomation(  # type: ignore[arg-type]
            {"login_required": True, "messages": [], "warnings": []}
        )
    )
    result = tool.execute({}, SimpleNamespace(summarize_private_text=lambda prompt: prompt))
    assert not result["success"]
    assert result["login_required"]


def test_inbox_summary_uses_main_profile_bridge_by_default() -> None:
    bridge = FakeBridge(
        {
            "login_required": False,
            "messages": [
                {
                    "number": "1",
                    "sender": "Person",
                    "subject": "Status",
                    "date": "Aug 14",
                    "body": "Everything is on track.",
                }
            ],
            "warnings": [],
        }
    )
    controller = FakeController()
    progress: list[str] = []
    tool = GmailInboxSummaryTool(  # type: ignore[arg-type]
        bridge=bridge,
        controller=controller,
    )
    result = tool.execute(
        {},
        SimpleNamespace(
            summarize_private_text=lambda _prompt: "Summary",
            report_progress=progress.append,
        ),
    )

    assert result["success"]
    assert controller.calls == [
        ("https://mail.google.com/mail/u/0/#search/in%3Ainbox", "Mail")
    ]
    assert bridge.calls == [("gmail_read_inbox", {"max_messages": 20}, 90)]
    assert any("Opening Gmail" in item for item in progress)
    assert any("Summarizing messages" in item for item in progress)


def test_inbox_summary_reuses_connected_gmail_tab() -> None:
    bridge = FakeConnectedBridge({"login_required": False, "messages": [], "warnings": []})
    controller = FakeController()
    progress: list[str] = []
    result = GmailInboxSummaryTool(  # type: ignore[arg-type]
        bridge=bridge,
        controller=controller,
    ).execute(
        {},
        SimpleNamespace(
            summarize_private_text=lambda _prompt: "Summary",
            report_progress=progress.append,
        ),
    )

    assert result["success"]
    assert controller.calls == []
    assert bridge.calls == [("gmail_read_inbox", {"max_messages": 20}, 90)]
    assert any("existing Gmail tab" in item for item in progress)


def test_inbox_summary_refuses_to_treat_one_read_as_the_whole_inbox() -> None:
    bridge = FakeConnectedBridge(
        {
            "login_required": False,
            "available_count": 20,
            "requested_count": 20,
            "messages": [
                {
                    "number": "1",
                    "sender": "Person",
                    "subject": "Only captured message",
                    "date": "Aug 14",
                    "body": "This must not become an inbox-wide summary.",
                }
            ],
            "warnings": [],
        }
    )
    summarizer_calls: list[str] = []
    result = GmailInboxSummaryTool(bridge=bridge, controller=FakeController()).execute(  # type: ignore[arg-type]
        {},
        SimpleNamespace(
            summarize_private_text=lambda prompt: summarizer_calls.append(prompt) or "wrong",
            report_progress=None,
        ),
    )

    assert not result["success"]
    assert result["count"] == 1
    assert "stopped" in result["error"]
    assert summarizer_calls == []
