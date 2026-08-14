const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitFor(selector, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = document.querySelector(selector);
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

async function restoreInbox() {
  if (location.hash !== "#search/in%3Ainbox") location.hash = "#search/in%3Ainbox";
  return waitFor("tr.zA", 30000);
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

  const count = Math.min(maxMessages, document.querySelectorAll("tr.zA").length);
  const messages = [];
  const warnings = [];
  for (let index = 0; index < count; index += 1) {
    const listReady = await restoreInbox();
    if (!listReady) break;
    const rows = [...document.querySelectorAll("tr.zA")];
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
    row.click();
    const firstBody = await waitFor(".a3s", 20000);
    if (!firstBody) {
      warnings.push(`Could not read message ${index + 1}: ${subjectHint}`);
      continue;
    }
    const bodies = [...document.querySelectorAll(".a3s")];
    const bodyNode = bodies.at(-1) || firstBody;
    messages.push({
      number: String(index + 1),
      sender: firstText(document, ["span.gD", "span[email]"], senderHint),
      subject: firstText(document, ["h2.hP", "[data-thread-perm-id]"], subjectHint),
      date: firstText(document, ["span.g3", "[aria-label*='date']"], dateHint),
      body: (bodyNode.textContent?.trim() || "").slice(0, 4000)
    });

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
  return { login_required: false, messages, warnings };
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
