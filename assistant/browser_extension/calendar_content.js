const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function loginRequired() {
  return location.hostname === "accounts.google.com" || Boolean(document.querySelector('input[type="email"]'));
}

async function waitFor(selector, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = document.querySelector(selector);
    if (found) return found;
    await delay(250);
  }
  return null;
}

function visible(element) {
  if (!(element instanceof Element)) return false;
  const style = getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
}

const MONTH_NUMBER = {
  january: "01", february: "02", march: "03", april: "04", may: "05", june: "06",
  july: "07", august: "08", september: "09", october: "10", november: "11", december: "12"
};

function parseCalendarDate(value, fallback) {
  const compact = String(value || "").match(/\b(\d{4})[-/]?(\d{2})[-/]?(\d{2})\b/);
  if (compact) return `${compact[1]}-${compact[2]}-${compact[3]}`;
  const named = String(value || "").match(
    /\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:,\s*(\d{4}))?\b/i
  );
  if (!named) return null;
  const year = named[3] || String(fallback || "").slice(0, 4);
  return year ? `${year}-${MONTH_NUMBER[named[1].toLowerCase()]}-${named[2].padStart(2, "0")}` : null;
}

function dateFromAncestor(node, fallback) {
  for (let current = node, depth = 0; current && depth < 14; current = current.parentElement, depth += 1) {
    for (const attribute of ["data-datekey", "data-date", "data-day", "data-startdate"]) {
      const parsed = parseCalendarDate(current.getAttribute(attribute), fallback);
      if (parsed) return { date: parsed, date_confident: true };
    }
  }
  return { date: fallback, date_confident: false };
}

function dateFromMonthGrid(node, fallback) {
  const eventRect = node.getBoundingClientRect();
  const x = eventRect.left + Math.min(8, Math.max(1, eventRect.width / 2));
  const y = eventRect.top + Math.max(1, Math.min(eventRect.height / 2, 12));
  for (const cell of document.querySelectorAll('[role="gridcell"],[data-datekey],[data-date]')) {
    if (!visible(cell)) continue;
    const parsed = parseCalendarDate(
      cell.getAttribute("data-datekey") || cell.getAttribute("data-date") || cell.getAttribute("aria-label"),
      fallback
    );
    if (!parsed) continue;
    const rect = cell.getBoundingClientRect();
    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      return { date: parsed, date_confident: true };
    }
  }
  return { date: fallback, date_confident: false };
}

async function readEvents(date) {
  if (loginRequired()) return { login_required: true, events: [] };
  // tabs.update() already waits for document completion. A short settling delay
  // is enough for Calendar's client-side route without adding 45+ seconds to a
  // month read.
  await delay(400);
  const seen = new Set();
  const events = [];
  for (const node of document.querySelectorAll("[data-eventid]")) {
    if (!visible(node)) continue;
    const eventId = node.getAttribute("data-eventid") || "";
    if (!eventId || seen.has(eventId)) continue;
    seen.add(eventId);
    const details = (node.getAttribute("aria-label") || node.textContent || "").trim();
    const fromAncestor = dateFromAncestor(node, date);
    const eventDate = fromAncestor.date_confident ? fromAncestor : dateFromMonthGrid(node, date);
    if (details) events.push({
      event_id: eventId,
      ...eventDate,
      details: details.slice(0, 800)
    });
  }
  return { login_required: false, events };
}

function findButton(name) {
  const expected = name.toLowerCase();
  return [...document.querySelectorAll('button,[role="button"]')].find((node) => {
    const label = (node.getAttribute("aria-label") || node.textContent || "").trim().toLowerCase();
    return visible(node) && label === expected;
  });
}

function findLabel(pattern) {
  const expression = new RegExp(pattern, "i");
  return [...document.querySelectorAll("input,textarea,[contenteditable=true]")].find((node) => {
    const label = `${node.getAttribute("aria-label") || ""} ${node.getAttribute("placeholder") || ""}`;
    return visible(node) && expression.test(label);
  });
}

function setValue(node, value) {
  node.focus();
  if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value")?.set;
    setter?.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    node.textContent = value;
    node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
  }
}

async function save() {
  const deadline = Date.now() + 20000;
  let button = findButton("Save");
  while (!button && Date.now() < deadline) {
    await delay(250);
    button = findButton("Save");
  }
  if (!button) throw new Error("Calendar's Save button could not be found; nothing was submitted.");
  button.click();
  await delay(1200);
  return { saved: true };
}

async function updateEvent(payload) {
  if (loginRequired()) return { login_required: true };
  await delay(1500);
  const escaped = CSS.escape(payload.event_id || "");
  const event = document.querySelector(`[data-eventid="${escaped}"]`);
  if (!event) throw new Error("The selected event is no longer present on that date.");
  event.click();
  await delay(600);
  const edit = [...document.querySelectorAll('[aria-label*="Edit"],button,[role="button"]')].find((node) =>
    visible(node) && /edit/i.test(node.getAttribute("aria-label") || node.textContent || "")
  );
  if (!edit) throw new Error("Calendar's Edit control could not be found; nothing changed.");
  edit.click();
  await delay(700);
  for (const field of payload.fields || []) {
    const node = findLabel(field.label);
    if (!node) throw new Error(`Calendar's ${field.name} field could not be found; nothing was saved.`);
    setValue(node, field.value);
  }
  return save();
}

async function execute(action, payload) {
  if (action === "calendar.read_day" || action === "calendar.read_month") return readEvents(payload.date);
  if (action === "calendar.create_event") {
    if (loginRequired()) return { login_required: true };
    await waitFor('button,[role="button"]');
    return save();
  }
  if (action === "calendar.update_event") return updateEvent(payload);
  throw new Error(`Unsupported Calendar action: ${action}`);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "thursday.action") return false;
  void execute(message.action, message.payload || {})
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ error: String(error?.message || error) }));
  return true;
});
void chrome.runtime.sendMessage({ type: "thursday.wake" });
