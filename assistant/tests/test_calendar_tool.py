from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from assistant.tools.calendar_tool import (
    CalendarCreateEventTool,
    EventRecord,
    _event_day_from_label,
    _event_times,
    _parse_datetime,
)


class FakeAgendaBridge:
    def __init__(self, events=None):
        self.events = events or []
        self.calls = 0

    def status(self):
        return {"connected": True}

    def request(self, action, payload, timeout=90):
        self.calls += 1
        assert action == "calendar.read_agenda"
        return {
            "login_required": False,
            "events": [
                {"event_id": item.event_id, "date": item.day.isoformat(), "details": item.label}
                for item in self.events
            ],
        }


def test_naive_calendar_datetime_defaults_to_india_timezone() -> None:
    parsed = _parse_datetime("2026-08-15T09:30:00")
    assert parsed.isoformat() == "2026-08-15T09:30:00+05:30"


def test_event_time_range_is_extracted_for_conflicts() -> None:
    start, end = _event_times("Team sync, 9:00 AM to 10:30 AM", datetime(2026, 8, 15).date())
    assert start and start.hour == 9
    assert end and end.hour == 10 and end.minute == 30


def test_calendar_accessible_label_supplies_real_event_date() -> None:
    fallback = datetime(2026, 9, 1).date()
    actual = _event_day_from_label(
        "Other's birthday, Sunday, September 27, All day", fallback
    )
    assert actual == datetime(2026, 9, 27).date()


def test_calendar_create_preview_can_be_rejected_without_write() -> None:
    existing = EventRecord(
        event_id="event",
        day=datetime(2026, 8, 15).date(),
        label="Existing, 9:00 AM to 10:00 AM",
        start=_parse_datetime("2026-08-15T09:00:00"),
        end=_parse_datetime("2026-08-15T10:00:00"),
    )
    bridge = FakeAgendaBridge([existing])
    prompts: list[str] = []
    context = SimpleNamespace(
        now=lambda: datetime.now(UTC),
        confirm=lambda prompt: prompts.append(prompt) or False,
    )
    result = CalendarCreateEventTool(bridge=bridge).execute(  # type: ignore[arg-type]
        {
            "title": "New meeting",
            "start": "2026-08-15T09:30:00",
            "end": "2026-08-15T10:30:00",
        },
        context,
    )
    assert not result["success"] and result["cancelled"]
    assert bridge.calls == 1
    assert "Possible conflict" in prompts[0]
