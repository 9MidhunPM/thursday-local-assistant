let reelTimer = null;

function stop() {
  const stopped = reelTimer !== null;
  if (reelTimer !== null) clearInterval(reelTimer);
  reelTimer = null;
  return stopped;
}

function advance() {
  if (document.visibilityState !== "visible" || !document.hasFocus()) return;
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", code: "ArrowDown", bubbles: true }));
  document.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowDown", code: "ArrowDown", bubbles: true }));
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "thursday.action") return false;
  if (message.action === "instagram.reels.start") {
    const alreadyRunning = reelTimer !== null;
    if (!alreadyRunning) {
      const interval = Math.max(5, Math.min(Number(message.payload?.interval_seconds) || 15, 120));
      reelTimer = setInterval(advance, interval * 1000);
    }
    sendResponse({ running: true, already_running: alreadyRunning });
    return false;
  }
  if (message.action === "instagram.reels.stop") {
    sendResponse({ stopped: stop() });
    return false;
  }
  sendResponse({ error: `Unsupported Instagram action: ${message.action}` });
  return false;
});
window.addEventListener("pagehide", stop);
void chrome.runtime.sendMessage({ type: "thursday.wake" });
