from __future__ import annotations

import re
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.ui_automation import BraveAutomationSession, get_browser_automation

TIMEZONE = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    day: date
    label: str
    start: datetime | None = None
    end: datetime | None = None


class EventReferenceStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, EventRecord] = {}
        self._counter = 0

    def add(self, record: EventRecord) -> str:
        with self._lock:
            for reference, existing in self._records.items():
                if existing.event_id == record.event_id and existing.day == record.day:
                    return reference
            self._counter += 1
            reference = f"cal-{self._counter}"
            self._records[reference] = record
            return reference

    def get(self, reference: str) -> EventRecord | None:
        with self._lock:
            return self._records.get(reference)


_EVENTS = EventReferenceStore()


def _parse_datetime(value: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed.astimezone(TIMEZONE)


def _parse_day(value: str | None, now: datetime) -> date:
    if value:
        return date.fromisoformat(value)
    return now.astimezone(TIMEZONE).date()


def _calendar_url(day: date) -> str:
    return f"https://calendar.google.com/calendar/u/0/r/agenda/{day.year}/{day.month}/{day.day}"


def _login_required(page: Any) -> bool:
    return "accounts.google.com" in page.url or bool(page.locator('input[type="email"]').count())


def _calendar_page(browser_context: Any) -> Any:
    return next(
        (
            page
            for page in browser_context.pages
            if "calendar.google.com" in page.url or "accounts.google.com" in page.url
        ),
        None,
    ) or browser_context.new_page()


_TIME_RANGE = re.compile(
    r"(?P<start>\d{1,2}(?::\d{2})?\s*[ap]m)\s*(?:to|[-–—])\s*"
    r"(?P<end>\d{1,2}(?::\d{2})?\s*[ap]m)",
    re.IGNORECASE,
)


def _time_from_label(value: str) -> time:
    normalized = re.sub(r"\s+", "", value).upper()
    if ":" not in normalized:
        normalized = normalized[:-2] + ":00" + normalized[-2:]
    return datetime.strptime(normalized, "%I:%M%p").time()


def _event_times(label: str, day: date) -> tuple[datetime | None, datetime | None]:
    match = _TIME_RANGE.search(label)
    if not match:
        return None, None
    start = datetime.combine(day, _time_from_label(match.group("start")), TIMEZONE)
    end = datetime.combine(day, _time_from_label(match.group("end")), TIMEZONE)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _load_agenda(browser_context: Any, start_day: date, days: int) -> dict[str, Any]:
    page = _calendar_page(browser_context)
    collected: list[EventRecord] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        page.goto(_calendar_url(day), wait_until="domcontentloaded", timeout=45_000)
        page.bring_to_front()
        if _login_required(page):
            return {"login_required": True, "events": []}
        try:
            page.wait_for_timeout(1_500)
            locators = page.locator("[data-eventid]")
            count = locators.count()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Google Calendar loaded, but its agenda could not be read.") from exc
        seen: set[str] = set()
        for index in range(count):
            event = locators.nth(index)
            event_id = event.get_attribute("data-eventid") or ""
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            label = (event.get_attribute("aria-label") or event.inner_text()).strip()
            if not label:
                continue
            event_start, event_end = _event_times(label, day)
            collected.append(EventRecord(event_id, day, label[:800], event_start, event_end))
    return {"login_required": False, "events": collected}


def _fill_label(page: Any, label_pattern: str, value: str) -> bool:
    pattern = re.compile(label_pattern, re.IGNORECASE)
    locator = page.get_by_label(pattern)
    if locator.count():
        locator.first.fill(value)
        return True
    return False


def _save_calendar_page(page: Any) -> None:
    save = page.get_by_role("button", name=re.compile(r"^Save$", re.IGNORECASE))
    if not save.count():
        save = page.locator('[aria-label="Save"]')
    if not save.count():
        raise RuntimeError("Calendar's Save button could not be found; no change was submitted.")
    save.first.click(timeout=10_000)
    page.wait_for_timeout(1_000)


@dataclass
class CalendarAgendaTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="calendar_agenda",
        description=(
            "Read a bounded Google Calendar agenda in the visible Brave automation profile. "
            "Defaults to today in Asia/Kolkata and supports at most 31 days."
        ),
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Optional YYYY-MM-DD date."},
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 31,
                    "default": 1,
                },
            },
            "required": [],
        },
    )
    automation: BraveAutomationSession | None = None

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        try:
            start_day = _parse_day(str(arguments.get("start_date") or "") or None, context.now())
            days = int(arguments.get("days", 1))
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": f"Invalid calendar range: {exc}"}
        if not 1 <= days <= 31:
            return {"success": False, "error": "Calendar reads must cover 1 to 31 days."}
        automation = self.automation or get_browser_automation()
        try:
            result = automation.run(lambda browser: _load_agenda(browser, start_day, days), 120)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        if result["login_required"]:
            return {
                "success": False,
                "login_required": True,
                "error": (
                    "Google Calendar is open in Thursday's Brave profile. Sign in once, then "
                    "retry the calendar request."
                ),
            }
        events = []
        for record in result["events"]:
            events.append(
                {
                    "ref": _EVENTS.add(record),
                    "date": record.day.isoformat(),
                    "details": record.label,
                }
            )
        return {
            "success": True,
            "timezone": "Asia/Kolkata",
            "start_date": start_day.isoformat(),
            "days": days,
            "count": len(events),
            "events": events,
        }


@dataclass
class CalendarCreateEventTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="calendar_create_event",
        description=(
            "Preview and, only after user confirmation, create a Google Calendar event. "
            "Naive date-times are interpreted in Asia/Kolkata."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 date-time."},
                "end": {"type": "string", "description": "ISO 8601 date-time."},
                "location": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "start", "end"],
        },
    )
    automation: BraveAutomationSession | None = None

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        title = str(arguments.get("title") or "").strip()
        try:
            start = _parse_datetime(str(arguments.get("start") or ""))
            end = _parse_datetime(str(arguments.get("end") or ""))
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": f"Invalid event date-time: {exc}"}
        if not title or end <= start:
            return {"success": False, "error": "A title and an end after the start are required."}
        automation = self.automation or get_browser_automation()
        try:
            agenda = automation.run(lambda browser: _load_agenda(browser, start.date(), 1), 90)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        if agenda["login_required"]:
            return {
                "success": False,
                "login_required": True,
                "error": "Sign in to Google Calendar in Thursday's Brave profile, then retry.",
            }
        conflicts = [
            event.label
            for event in agenda["events"]
            if event.start and event.end and start < event.end and end > event.start
        ]
        preview = (
            f"Create '{title}' on {start:%A, %d %B %Y} from {start:%I:%M %p} "
            f"to {end:%I:%M %p} Asia/Kolkata"
        )
        if conflicts:
            preview += f". Possible conflict: {conflicts[0]}"
        if not context.confirm(preview + "?"):
            return {"success": False, "cancelled": True, "error": "Calendar creation cancelled."}

        params = {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}",
            "ctz": "Asia/Kolkata",
        }
        for key in ("location", "description"):
            value = str(arguments.get(key) or "").strip()
            if value:
                params["details" if key == "description" else key] = value
        url = "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)

        def create(browser: Any) -> None:
            page = _calendar_page(browser)
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if _login_required(page):
                raise RuntimeError("Google Calendar login expired before the event was created.")
            _save_calendar_page(page)

        try:
            automation.run(create, 90)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "created": True,
            "timezone": "Asia/Kolkata",
            "output": preview + ".",
            "conflicts": conflicts,
        }


@dataclass
class CalendarUpdateEventTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="calendar_update_event",
        description=(
            "Update an event returned by calendar_agenda using its cal-N reference. Shows an "
            "exact preview and requires confirmation before saving."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_ref": {"type": "string"},
                "title": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 date-time."},
                "end": {"type": "string", "description": "ISO 8601 date-time."},
                "location": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["event_ref"],
        },
    )
    automation: BraveAutomationSession | None = None

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        reference = str(arguments.get("event_ref") or "").strip()
        record = _EVENTS.get(reference)
        if record is None:
            return {
                "success": False,
                "error": "Unknown calendar reference. Read the relevant agenda again first.",
            }
        supplied = {
            key: str(arguments[key]).strip()
            for key in ("title", "start", "end", "location", "description")
            if key in arguments
        }
        if not supplied:
            return {"success": False, "error": "At least one event change is required."}
        if ("start" in supplied) != ("end" in supplied):
            return {"success": False, "error": "Provide both start and end when rescheduling."}
        parsed_start = parsed_end = None
        if "start" in supplied:
            try:
                parsed_start = _parse_datetime(supplied["start"])
                parsed_end = _parse_datetime(supplied["end"])
            except ValueError as exc:
                return {"success": False, "error": f"Invalid event date-time: {exc}"}
            if parsed_end <= parsed_start:
                return {"success": False, "error": "The event end must be after its start."}
        automation = self.automation or get_browser_automation()
        inspection_day = parsed_start.date() if parsed_start else record.day
        try:
            def inspect(browser: Any) -> dict[str, Any]:
                current = _load_agenda(browser, record.day, 1)
                target = (
                    current
                    if inspection_day == record.day or current["login_required"]
                    else _load_agenda(browser, inspection_day, 1)
                )
                return {"current": current, "target": target}

            inspection = automation.run(inspect, 120)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        current = inspection["current"]
        inspected = inspection["target"]
        if current["login_required"] or inspected["login_required"]:
            return {
                "success": False,
                "login_required": True,
                "error": "Sign in to Google Calendar in Thursday's Brave profile, then retry.",
            }
        if not any(event.event_id == record.event_id for event in current["events"]):
            return {
                "success": False,
                "error": "The selected event is no longer present. Read the agenda again.",
            }
        conflicts: list[str] = []
        if parsed_start and parsed_end:
            conflicts = [
                event.label
                for event in inspected["events"]
                if event.event_id != record.event_id
                and event.start
                and event.end
                and parsed_start < event.end
                and parsed_end > event.start
            ]
        changes = ", ".join(f"{key}={value!r}" for key, value in supplied.items())
        preview = f"Update {reference} ({record.label}) with {changes}"
        if conflicts:
            preview += f". Possible conflict: {conflicts[0]}"
        if not context.confirm(preview + "?"):
            return {"success": False, "cancelled": True, "error": "Calendar update cancelled."}

        def update(browser: Any) -> None:
            page = _calendar_page(browser)
            page.goto(_calendar_url(record.day), wait_until="domcontentloaded", timeout=45_000)
            if _login_required(page):
                raise RuntimeError("Google Calendar login expired before the event was updated.")
            page.wait_for_timeout(1_500)
            event = page.locator(f'[data-eventid="{record.event_id}"]')
            if not event.count():
                raise RuntimeError("The selected event is no longer present on that date.")
            event.first.click(timeout=10_000)
            edit = page.get_by_label(re.compile(r"Edit event", re.IGNORECASE))
            if not edit.count():
                edit = page.get_by_role("button", name=re.compile(r"Edit", re.IGNORECASE))
            if not edit.count():
                raise RuntimeError("Calendar's Edit control could not be found; nothing changed.")
            edit.first.click(timeout=10_000)
            page.wait_for_timeout(500)
            if "title" in supplied and not _fill_label(page, r"^Title$", supplied["title"]):
                raise RuntimeError("Calendar's title field could not be found; nothing was saved.")
            if parsed_start and parsed_end:
                fields = (
                    (r"Start date", parsed_start.strftime("%m/%d/%Y")),
                    (r"Start time", parsed_start.strftime("%I:%M%p")),
                    (r"End date", parsed_end.strftime("%m/%d/%Y")),
                    (r"End time", parsed_end.strftime("%I:%M%p")),
                )
                for label, value in fields:
                    if not _fill_label(page, label, value):
                        raise RuntimeError(f"Calendar's {label.lower()} field could not be found.")
            if "location" in supplied:
                if not _fill_label(page, r"location", supplied["location"]):
                    raise RuntimeError("Calendar's location field could not be found.")
            if "description" in supplied:
                if not _fill_label(page, r"description", supplied["description"]):
                    raise RuntimeError("Calendar's description field could not be found.")
            _save_calendar_page(page)

        try:
            automation.run(update, 120)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "updated": True,
            "output": preview + ".",
            "conflicts": conflicts,
        }


def get_tools() -> list[BaseTool]:
    return [CalendarAgendaTool(), CalendarCreateEventTool(), CalendarUpdateEventTool()]
