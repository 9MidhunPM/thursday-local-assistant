import urllib.parse
from types import SimpleNamespace

from assistant.logging_utils import redact_private_request_text
from assistant.security import redact_secrets
from assistant.tools.browser_control import BraveController
from assistant.tools.gmail_tool import GmailComposeTool


class FakeBrowser:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    def open_url(self, url: str, title_hint: str | None = None):
        self.calls.append((url, title_hint))
        return True, None


class FakeBridge:
    def __init__(self, connected: bool = False):
        self.connected = connected
        self.calls = []

    def status(self):
        return {"connected": self.connected}

    def wait_until_connected(self, timeout=20):
        self.connected = True
        return True

    def request(self, action, payload, timeout=90):
        self.calls.append((action, payload, timeout))
        return {"ready": True}


def test_gmail_compose_creates_draft_but_never_sends():
    browser = FakeBrowser()
    bridge = FakeBridge()
    tool = GmailComposeTool(controller=browser, bridge=bridge)  # type: ignore[arg-type]
    result = tool.execute(
        {
            "recipient": "person@example.com",
            "subject": "Project update",
            "body": "Hello,\n\nHere is the update.",
        },
        SimpleNamespace(),  # type: ignore[arg-type]
    )
    assert result["success"]
    assert result["drafted"] is True
    assert result["sent"] is False
    assert len(browser.calls) == 1
    assert bridge.calls[0][0] == "gmail.open_draft"
    assert "to=person%40example.com" in bridge.calls[0][1]["url"]


def test_gmail_compose_rejects_invalid_recipient_before_browser_control():
    browser = FakeBrowser()
    bridge = FakeBridge()
    tool = GmailComposeTool(controller=browser, bridge=bridge)  # type: ignore[arg-type]
    result = tool.execute(
        {"recipient": "not-an-email", "subject": "Hi", "body": "Body"},
        SimpleNamespace(),  # type: ignore[arg-type]
    )
    assert not result["success"]
    assert browser.calls == []


def test_email_draft_fields_are_redacted_from_logs():
    redacted = redact_secrets(
        {"recipient": "person@example.com", "subject": "Private", "body": "Secret body"}
    )
    assert set(redacted.values()) == {"***REDACTED***"}
    assert redact_private_request_text("Send an email saying private news") == (
        "[email draft request redacted]"
    )


def test_browser_draft_uses_prefilled_compose_url_and_never_types_send(monkeypatch):
    controller = BraveController()
    opened: dict[str, str | None] = {}

    def open_url(url: str, title_hint: str | None = None):
        opened["url"] = url
        opened["title_hint"] = title_hint
        return True, None

    monkeypatch.setattr(controller, "open_url", open_url)
    monkeypatch.setattr(
        controller,
        "_send_key",
        lambda key, modifiers=(): (_ for _ in ()).throw(AssertionError("must not type keys")),
    )

    success, error = controller.fill_gmail_draft("to@example.com", "Subject", "Body")
    assert success and error is None
    parsed = urllib.parse.urlparse(str(opened["url"]))
    params = urllib.parse.parse_qs(parsed.query)
    assert params == {
        "view": ["cm"],
        "fs": ["1"],
        "to": ["to@example.com"],
        "su": ["Subject"],
        "body": ["Body"],
    }
    assert opened["title_hint"] == "Mail"


def test_open_url_reuses_existing_matching_brave_window(monkeypatch):
    controller = BraveController()
    monkeypatch.setattr(controller, "_binary", lambda: "/usr/bin/brave")
    monkeypatch.setattr(controller, "focus", lambda title_hint=None: (True, None))
    monkeypatch.setattr(
        "assistant.tools.browser_control.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not open a tab")),
    )

    assert controller.open_url("https://mail.google.com", title_hint="Mail") == (True, None)


def test_open_url_starts_plain_brave_without_extension_flags(monkeypatch):
    controller = BraveController()
    focus_results = iter([(False, "not open"), (True, None)])
    launched: list[list[str]] = []
    monkeypatch.setattr(controller, "_binary", lambda: "/usr/bin/brave")
    monkeypatch.setattr(controller, "focus", lambda title_hint=None: next(focus_results))
    monkeypatch.setattr(
        "assistant.tools.browser_control.subprocess.Popen",
        lambda args, **_kwargs: launched.append(args),
    )
    assert controller.open_url("https://mail.google.com", title_hint="Mail") == (True, None)
    assert launched == [["/usr/bin/brave", "https://mail.google.com"]]
