const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function isVisible(element) {
  if (!(element instanceof Element)) return false;
  const style = getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
}

function visibleNodes(selector, root = document) {
  return [...root.querySelectorAll(selector)].filter(isVisible);
}

async function waitForVisible(selector, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = visibleNodes(selector).at(-1);
    if (found) return found;
    await delay(250);
  }
  return null;
}

function firstText(root, selectors, fallback = "") {
  for (const selector of selectors) {
    const value = root.querySelector(selector)?.textContent?.trim();
    if (value) return value;
  }
  return fallback;
}

function firstVisibleText(root, selectors, fallback = "") {
  for (const selector of selectors) {
    const node = visibleNodes(selector, root).at(-1);
    const value = node?.textContent?.trim();
    if (value) return value;
  }
  return fallback;
}

function currentMessageMarker() {
  const subject = firstVisibleText(document, ["h2.hP", "[data-thread-perm-id]"]);
  const sender = firstVisibleText(document, ["span.gD", "span[email]"]);
  const body = visibleNodes(".a3s").at(-1)?.textContent?.trim() || "";
  return `${subject}\n${sender}\n${body.slice(0, 500)}`;
}

async function waitForOpenedMessage(
  previousMarker,
  subjectHint,
  allowUnchanged = false,
  timeoutMs = 20000
) {
  const deadline = Date.now() + timeoutMs;
  const startedAt = Date.now();
  const expected = subjectHint.trim().toLowerCase();
  while (Date.now() < deadline) {
    const body = visibleNodes(".a3s").at(-1);
    const subject = firstVisibleText(document, ["h2.hP", "[data-thread-perm-id]"]);
    const marker = currentMessageMarker();
    const normalizedSubject = subject.toLowerCase();
    const subjectMatches =
      !expected || normalizedSubject.includes(expected) || expected.includes(normalizedSubject);
    const settledInitialMessage = allowUnchanged && Date.now() - startedAt >= 500;
    if (body && marker && (marker !== previousMarker || settledInitialMessage) && subjectMatches) {
      return body;
    }
    await delay(250);
  }
  return null;
}

async function restoreInbox() {
  if (location.hash !== "#search/in%3Ainbox") location.hash = "#search/in%3Ainbox";
  return waitForVisible("tr.zA", 30000);
}

async function readInbox(maxMessages) {
  const firstRow = await restoreInbox();
  if (!firstRow) {
    const emptyText = document.body?.innerText || "";
    if (/no (?:emails|mail)/i.test(emptyText)) {
      return { login_required: false, messages: [], warnings: [] };
    }
    throw new Error("Gmail loaded, but Thursday could not find the inbox message list.");
  }

  const availableCount = visibleNodes("tr.zA").length;
  const count = Math.min(maxMessages, availableCount);
  const messages = [];
  const warnings = [];
  const seen = new Set();
  for (let index = 0; index < count; index += 1) {
    const listReady = await restoreInbox();
    if (!listReady) break;
    const rows = visibleNodes("tr.zA");
    const row = rows[index];
    if (!row) break;
    const wasUnread = row.classList.contains("zE");
    const subjectHint = firstText(
      row,
      ["span.bog", "[data-thread-id] span", "td:nth-child(6)"],
      `Message ${index + 1}`
    );
    const senderHint = firstText(row, ["span[email]", ".yX.xY span", "td:nth-child(5)"]);
    const dateHint = firstText(row, ["td.xW span", "td:last-child"]);
    const previousMarker = currentMessageMarker();
    row.click();
    const firstBody = await waitForOpenedMessage(previousMarker, subjectHint, index === 0, 20000);
    if (!firstBody) {
      warnings.push(`Could not read message ${index + 1}: ${subjectHint}`);
      continue;
    }
    const bodyNode = visibleNodes(".a3s").at(-1) || firstBody;
    const message = {
      number: String(index + 1),
      sender: firstVisibleText(document, ["span.gD", "span[email]"], senderHint),
      subject: firstVisibleText(document, ["h2.hP", "[data-thread-perm-id]"], subjectHint),
      date: firstVisibleText(document, ["span.g3", "[aria-label*='date']"], dateHint),
      body: (bodyNode.textContent?.trim() || "").slice(0, 4000)
    };
    const messageKey = `${message.sender}\n${message.subject}\n${message.date}\n${message.body}`;
    if (seen.has(messageKey)) {
      warnings.push(`Gmail repeated message ${index + 1}; it was excluded from the summary.`);
    } else {
      seen.add(messageKey);
      messages.push(message);
    }

    if (wasUnread) {
      const unreadButton = document.querySelector(
        '[aria-label^="Mark as unread"], [data-tooltip^="Mark as unread"], [title^="Mark as unread"]'
      );
      if (unreadButton) {
        unreadButton.click();
        await delay(500);
      } else {
        warnings.push(`Could not restore unread state for: ${subjectHint}`);
      }
    }
  }
  await restoreInbox();
  return {
    login_required: false,
    messages,
    warnings,
    available_count: availableCount,
    requested_count: count
  };
}

async function execute(command) {
  if (command.action !== "gmail_read_inbox") {
    throw new Error(`Unsupported browser action: ${command.action}`);
  }
  const maxMessages = Math.max(1, Math.min(Number(command.payload?.max_messages) || 20, 20));
  return readInbox(maxMessages);
}

const bridgeFrame = document.createElement("iframe");
bridgeFrame.hidden = true;
bridgeFrame.setAttribute("aria-hidden", "true");
bridgeFrame.src = chrome.runtime.getURL("bridge_frame.html");
(document.documentElement || document.body).appendChild(bridgeFrame);

window.addEventListener("message", async (event) => {
  if (event.source !== bridgeFrame.contentWindow) return;
  const command = event.data;
  if (!command || command.type !== "thursday-command") return;
  let result;
  try {
    result = { id: command.id, success: true, data: await execute(command) };
  } catch (error) {
    result = { id: command.id, success: false, error: String(error?.message || error) };
  }
  bridgeFrame.contentWindow?.postMessage(
    { type: "thursday-command-result", ...result },
    chrome.runtime.getURL("").slice(0, -1)
  );
});
