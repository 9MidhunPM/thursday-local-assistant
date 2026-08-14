from __future__ import annotations

import urllib.parse

from assistant.integrations.mailto_handler import DESKTOP_ID, desktop_entry, gmail_compose_url


def test_mailto_becomes_populated_gmail_compose_url() -> None:
    url = gmail_compose_url(
        "mailto:person%40example.com?subject=Hello%20there&body=Line%201%0ALine%202"
        "&cc=copy%40example.com&bcc=hidden%40example.com"
    )
    parsed = urllib.parse.urlparse(url)
    assert parsed.netloc == "mail.google.com"
    assert urllib.parse.parse_qs(parsed.query) == {
        "view": ["cm"],
        "fs": ["1"],
        "to": ["person@example.com"],
        "cc": ["copy@example.com"],
        "bcc": ["hidden@example.com"],
        "su": ["Hello there"],
        "body": ["Line 1\nLine 2"],
    }


def test_mailto_handler_rejects_other_schemes() -> None:
    try:
        gmail_compose_url("https://example.com")
    except ValueError as exc:
        assert "mailto" in str(exc)
    else:
        raise AssertionError("non-mailto URL was accepted")


def test_desktop_entry_registers_only_mailto() -> None:
    entry = desktop_entry("/tmp/venv/bin/python")
    assert DESKTOP_ID == "thursday-brave-mail.desktop"
    assert "MimeType=x-scheme-handler/mailto;" in entry
    assert "x-scheme-handler/http" not in entry
    assert "Exec=/tmp/venv/bin/python -m assistant.integrations.mailto_handler %U" in entry
