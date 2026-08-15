importScripts("config.js");

const BRIDGE_URL = "http://127.0.0.1:5005/api/browser-bridge/v2";
const CAPABILITIES = [
  "gmail.read_inbox",
  "gmail.open_draft",
  "calendar.read_agenda",
  "calendar.create_event",
  "calendar.update_event",
  "instagram.reels.start",
  "instagram.reels.stop"
];
const SITE = {
  gmail: "https://mail.google.com/mail/u/0/",
  calendar: "https://calendar.google.com/calendar/u/0/r/agenda",
  instagram: "https://www.instagram.com/reels/"
};
let polling = false;
let lastError = null;
let lastAction = null;

function headers() {
  return {
    "Content-Type": "application/json",
    "X-Thursday-Helper-Token": globalThis.THURSDAY_HELPER_CONFIG.token,
    "X-Thursday-Helper-Version": globalThis.THURSDAY_HELPER_CONFIG.version,
    "X-Thursday-Helper-Capabilities": CAPABILITIES.join(",")
  };
}

function matchesSite(tab, site) {
  try {
    return new URL(tab.url || "").hostname === new URL(SITE[site]).hostname;
  } catch (_) {
    return false;
  }
}

async function findOrCreateTab(site) {
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((tab) => matchesSite(tab, site));
  if (existing?.id) return existing;
  return chrome.tabs.create({ url: SITE[site], active: true });
}

function waitForLoaded(tabId, timeoutMs = 8000, settleMs = 600) {
  return new Promise(async (resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    const settleDeadline = Date.now() + settleMs;
    while (Date.now() < deadline) {
      try {
        const tab = await chrome.tabs.get(tabId);
        // Google Calendar continuously loads background resources, leaving the
        // tab in "loading" long after its DOM is interactive. The subsequent
        // content-script send has its own readiness retry, so don't block a
        // Calendar request on that non-essential browser status forever.
        if (tab.status === "complete" || Date.now() >= settleDeadline) {
          resolve(tab);
          return;
        }
      } catch (error) {
        reject(error);
        return;
      }
      await new Promise((done) => setTimeout(done, 250));
    }
    reject(new Error("The browser page did not finish loading in time."));
  });
}

function missingTab(error) {
  return /no tab with id|invalid tab id|tab was closed/i.test(String(error?.message || error || ""));
}

async function navigate(site, tab, url) {
  try {
    const updated = await chrome.tabs.update(tab.id, { url, active: true });
    await waitForLoaded(updated.id);
    return chrome.tabs.get(updated.id);
  } catch (error) {
    // Calendar and Gmail tabs can be closed by the user or replaced by Google's
    // sign-in flow between query() and update(). Never keep retrying a dead id.
    if (!missingTab(error)) throw error;
    const replacement = await chrome.tabs.create({ url, active: true });
    await waitForLoaded(replacement.id);
    return chrome.tabs.get(replacement.id);
  }
}

async function send(tabId, action, payload = {}) {
  let finalError;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      const result = await chrome.tabs.sendMessage(tabId, { type: "thursday.action", action, payload });
      if (result?.error) throw new Error(result.error);
      return result;
    } catch (error) {
      finalError = error;
      if (missingTab(error)) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(String(finalError?.message || finalError || "Thursday content helper was unavailable."));
}

async function navigateAndSend(site, tab, url, action, payload = {}) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    tab = await navigate(site, tab, url);
    try {
      return { tab, result: await send(tab.id, action, payload) };
    } catch (error) {
      if (!missingTab(error) || attempt > 0) throw error;
      tab = await chrome.tabs.create({ url, active: true });
    }
  }
  throw new Error("The browser tab closed before Thursday could finish the action.");
}

async function execute(command) {
  const { action, payload = {} } = command;
  if (!CAPABILITIES.includes(action)) throw new Error(`Unsupported helper action: ${action}`);
  lastAction = action;
  if (action === "gmail.read_inbox") {
    let tab = await findOrCreateTab("gmail");
    const page = await navigateAndSend(
      "gmail", tab, payload.url || `${SITE.gmail}#search/in%3Ainbox`, action, payload
    );
    tab = page.tab;
    if (new URL(tab.url).hostname === "accounts.google.com") return { login_required: true, messages: [], warnings: [] };
    return page.result;
  }
  if (action === "gmail.open_draft") {
    let tab = await findOrCreateTab("gmail");
    const page = await navigateAndSend("gmail", tab, payload.url, action, payload);
    tab = page.tab;
    if (new URL(tab.url).hostname === "accounts.google.com") return { login_required: true };
    return page.result;
  }
  if (action === "calendar.read_agenda") {
    let tab = await findOrCreateTab("calendar");
    const events = [];
    for (const target of payload.targets || []) {
      const readAction = target.mode === "month" ? "calendar.read_month" : "calendar.read_day";
      const page = await navigateAndSend(
        "calendar", tab, target.url, readAction, { date: target.date }
      );
      tab = page.tab;
      if (new URL(tab.url).hostname === "accounts.google.com") return { login_required: true, events: [] };
      const result = page.result;
      if (result.login_required) return { login_required: true, events: [] };
      events.push(...(result.events || []));
    }
    return { login_required: false, events };
  }
  if (action === "calendar.create_event") {
    let tab = await findOrCreateTab("calendar");
    const page = await navigateAndSend("calendar", tab, payload.url, action, payload);
    tab = page.tab;
    if (new URL(tab.url).hostname === "accounts.google.com") return { login_required: true };
    return page.result;
  }
  if (action === "calendar.update_event") {
    let tab = await findOrCreateTab("calendar");
    const page = await navigateAndSend("calendar", tab, payload.url, action, payload);
    tab = page.tab;
    if (new URL(tab.url).hostname === "accounts.google.com") return { login_required: true };
    return page.result;
  }
  const tab = await findOrCreateTab("instagram");
  await chrome.tabs.update(tab.id, { active: true });
  return send(tab.id, action, payload);
}

async function reportResult(result) {
  const response = await fetch(`${BRIDGE_URL}/result`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(result)
  });
  if (!response.ok) throw new Error(`Thursday bridge returned HTTP ${response.status}.`);
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    while (true) {
      const query = new URLSearchParams({
        version: globalThis.THURSDAY_HELPER_CONFIG.version,
        capabilities: CAPABILITIES.join(",")
      });
      const response = await fetch(`${BRIDGE_URL}/next?${query}`, {
        cache: "no-store",
        headers: headers()
      });
      if (response.status === 204) continue;
      if (!response.ok) throw new Error(`Thursday bridge returned HTTP ${response.status}.`);
      const command = await response.json();
      try {
        const data = await execute(command);
        await reportResult({ id: command.id, success: true, data });
        lastError = null;
      } catch (error) {
        lastError = String(error?.message || error);
        await reportResult({ id: command.id, success: false, error: lastError });
      }
    }
  } catch (error) {
    lastError = String(error?.message || error);
  } finally {
    polling = false;
  }
}

chrome.runtime.onInstalled.addListener(() => void poll());
chrome.runtime.onStartup.addListener(() => void poll());
chrome.alarms.create("thursday-bridge", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => void poll());
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "thursday.wake") {
    void poll();
    sendResponse({ ok: true });
  } else if (message?.type === "thursday.status") {
    sendResponse({ connected: polling, version: globalThis.THURSDAY_HELPER_CONFIG.version, lastAction, lastError });
  }
});
void poll();
