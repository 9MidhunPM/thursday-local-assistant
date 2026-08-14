const BRIDGE_URL = "http://127.0.0.1:5005/api/browser-bridge";
const GMAIL_ORIGIN = "https://mail.google.com";
const pending = new Map();

async function nextCommand() {
  const response = await fetch(`${BRIDGE_URL}/next`, { cache: "no-store" });
  if (response.status === 204) return null;
  if (!response.ok) throw new Error(`Bridge returned HTTP ${response.status}`);
  return response.json();
}

async function reportResult(result) {
  const response = await fetch(`${BRIDGE_URL}/result`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(result)
  });
  if (!response.ok) throw new Error(`Bridge returned HTTP ${response.status}`);
}

function executeInGmail(command) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pending.delete(command.id);
      reject(new Error("Gmail did not finish reading the inbox in time."));
    }, 85000);
    pending.set(command.id, { resolve, timeout });
    parent.postMessage({ type: "thursday-command", ...command }, GMAIL_ORIGIN);
  });
}

window.addEventListener("message", (event) => {
  if (event.source !== parent || event.origin !== GMAIL_ORIGIN) return;
  const message = event.data;
  if (!message || message.type !== "thursday-command-result") return;
  const waiting = pending.get(message.id);
  if (!waiting) return;
  clearTimeout(waiting.timeout);
  pending.delete(message.id);
  waiting.resolve(message);
});

async function poll() {
  while (true) {
    try {
      const command = await nextCommand();
      if (!command) continue;
      try {
        await reportResult(await executeInGmail(command));
      } catch (error) {
        await reportResult({
          id: command.id,
          success: false,
          error: String(error?.message || error)
        });
      }
    } catch (_) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
}

void poll();
